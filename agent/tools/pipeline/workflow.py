from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from .connectors.base import DataConnector
from .connectors.strava import StravaConnector, _MAX_SYNC_PAGES, _PER_PAGE
from .research_wiki_agent import run_deep_research_wiki_agent
from .storage_backend import ArtifactStore, AthleteStateStore, utc_now_iso


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_date(target_date: str | None) -> str:
    if isinstance(target_date, str) and target_date.strip():
        return target_date.strip()
    return datetime.now(timezone.utc).date().isoformat()


def _compact_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _activity_day(activity: dict[str, Any]) -> str:
    date_local = str(activity.get("start_date_local") or "").strip()
    if len(date_local) >= 10:
        return date_local[:10]

    date_utc = str(activity.get("start_date") or "").strip()
    if len(date_utc) >= 10:
        return date_utc[:10]

    return datetime.now(timezone.utc).date().isoformat()


def _resolve_targets(state_store: AthleteStateStore, athlete_ids_csv: str = "") -> list[dict[str, Any]]:
    candidate_ids: list[int] = []
    if athlete_ids_csv.strip():
        for value in athlete_ids_csv.split(","):
            athlete_id = _safe_int(value.strip(), 0)
            if athlete_id > 0:
                candidate_ids.append(athlete_id)

    if candidate_ids:
        targets: list[dict[str, Any]] = []
        for athlete_id in candidate_ids:
            payload = state_store.get_athlete(athlete_id) or {}
            if not isinstance(payload, dict):
                payload = {}
            payload["athlete_id"] = athlete_id
            targets.append(payload)
        return targets

    return state_store.list_athletes_with_tokens()


def _extract_activity_id_from_path(path: str) -> int:
    filename = path.split("/")[-1]
    match = re.match(r"(\d+)_", filename)
    if not match:
        return 0
    return _safe_int(match.group(1), 0)


def _latest_activity_paths_for_day(
    artifact_store: ArtifactStore,
    athlete_id: int,
    day: str,
) -> list[str]:
    prefix = f"raw/athletes/{athlete_id}/activities/{day}/"
    paths = artifact_store.list_paths(prefix, suffix=".json")

    by_activity_id: dict[int, str] = {}
    for path in paths:
        activity_id = _extract_activity_id_from_path(path)
        if activity_id <= 0:
            continue
        current = by_activity_id.get(activity_id)
        if current is None or path > current:
            by_activity_id[activity_id] = path

    return sorted(by_activity_id.values())

def run_ingestion(
    connector: DataConnector,
    athlete_ids_csv: str = "",
    lookback_days: int = 7,
    max_pages: int = _MAX_SYNC_PAGES,
    per_page: int = _PER_PAGE,
) -> dict[str, Any]:
    """Pipeline de ingesta agnóstico del servicio.

    Delega la obtención de datos al ``connector`` y gestiona almacenamiento
    y estado de sincronización en las capas comunes (ArtifactStore, AthleteStateStore).
    """
    state_store = AthleteStateStore()
    artifact_store = ArtifactStore()

    if athlete_ids_csv.strip():
        targets = _resolve_targets(state_store, athlete_ids_csv)
    else:
        targets = connector.list_syncable_athletes()

    now_epoch = int(datetime.now(timezone.utc).timestamp())
    lookback_seconds = max(1, int(lookback_days)) * 24 * 60 * 60

    run_report: dict[str, Any] = {
        "stage": "ingestion",
        "connector": connector.connector_name,
        "started_at": utc_now_iso(),
        "store_mode": artifact_store.mode,
        "state_mode": state_store.mode,
        "athletes": [],
        "errors": [],
    }

    for target in targets:
        athlete_id = _safe_int(target.get("athlete_id"), 0)
        if athlete_id <= 0:
            run_report["errors"].append({"athlete_id": athlete_id, "error": "missing_athlete_id"})
            continue

        previous_sync = state_store.get_last_sync_epoch(athlete_id)
        after_epoch = max(previous_sync or 0, now_epoch - lookback_seconds)
        sync_stamp = _compact_timestamp()

        athlete_report: dict[str, Any] = {
            "athlete_id": athlete_id,
            "after_epoch": after_epoch,
            "stored_activities": 0,
            "manifest_path": None,
        }

        # Refresh profile in state (best-effort)
        profile_payload = connector.get_athlete_profile(athlete_id)
        if isinstance(profile_payload, dict):
            state_store.upsert_tokens(
                athlete_id,
                {
                    "access_token": target.get("access_token"),
                    "refresh_token": target.get("refresh_token"),
                    "expires_at": target.get("expires_at"),
                    "scope": target.get("scope"),
                    "athlete": profile_payload,
                },
            )

        try:
            activities = connector.fetch_activities(
                athlete_id,
                after_epoch=after_epoch,
                max_pages=max_pages,
                per_page=per_page,
            )

            for activity in activities:
                activity_id = _safe_int(activity.get("id"), 0)
                if activity_id <= 0:
                    continue
                activity_day = _activity_day(activity)
                relative_path = (
                    f"raw/athletes/{athlete_id}/activities/{activity_day}/{activity_id}_{sync_stamp}.json"
                )
                artifact_store.write_json(relative_path, activity)
                athlete_report["stored_activities"] += 1

            manifest_path = f"raw/athletes/{athlete_id}/manifests/{_normalize_date(None)}_{sync_stamp}.json"
            artifact_store.write_json(
                manifest_path,
                {
                    "athlete_id": athlete_id,
                    "after_epoch": after_epoch,
                    "stored_activities": athlete_report["stored_activities"],
                    "created_at": utc_now_iso(),
                },
            )
            athlete_report["manifest_path"] = manifest_path

            state_store.update_sync_state(
                athlete_id,
                last_sync_epoch=now_epoch,
                status="success",
                details={"stored_activities": athlete_report["stored_activities"]},
            )
            run_report["athletes"].append(athlete_report)
        except Exception as exc:  # noqa: BLE001
            state_store.update_sync_state(
                athlete_id,
                last_sync_epoch=now_epoch,
                status="failed",
                details={"error": str(exc)},
            )
            run_report["errors"].append({"athlete_id": athlete_id, "error": str(exc)})

    run_report["finished_at"] = utc_now_iso()
    run_report["ok"] = not run_report["errors"]
    return run_report


def run_strava_ingestion(
    athlete_ids_csv: str = "",
    lookback_days: int = 7,
    max_pages: int = _MAX_SYNC_PAGES,
    per_page: int = _PER_PAGE,
) -> dict[str, Any]:
    """Wrapper de compatibilidad que usa el conector de Strava."""
    state_store = AthleteStateStore()
    connector = StravaConnector(state_store)
    return run_ingestion(
        connector,
        athlete_ids_csv=athlete_ids_csv,
        lookback_days=lookback_days,
        max_pages=max_pages,
        per_page=per_page,
    )


_RESEARCH_INPUT_PREFIX = "pipeline/research-wiki-input"


def _build_activity_metadata(activity: dict[str, Any], summary: str, athlete_id: int) -> dict[str, Any]:
    metadata: dict[str, Any] = {"athlete_id": athlete_id, "summary": summary}
    flat_fields = [
        "id", "name", "distance", "moving_time", "elapsed_time", "total_elevation_gain",
        "type", "sport_type", "workout_type", "device_name", "start_date", "start_date_local",
        "timezone", "average_speed", "max_speed", "average_temp", "average_watts",
        "kilojoules", "has_heartrate", "average_heartrate", "max_heartrate",
        "elev_high", "elev_low", "pr_count", "achievement_count", "kudos_count",
        "athlete_count", "gear_id", "trainer", "commute", "manual",
    ]
    for field in flat_fields:
        value = activity.get(field)
        if value is not None:
            metadata[field] = value
    return metadata


# ─── Stage 4: Deep Research Wiki ────────────────────────────────────────────

def _window_days(target_day: str, window_days: int) -> list[str]:
    try:
        anchor = datetime.strptime(target_day, "%Y-%m-%d").date()
    except ValueError:
        anchor = datetime.now(timezone.utc).date()

    days: list[str] = []
    span = max(2, int(window_days))
    for offset in range(span):
        days.append((anchor - timedelta(days=offset)).isoformat())
    return sorted(days)


def _latest_research_input_path_for_day(
    artifact_store: ArtifactStore,
    athlete_id: int,
    day: str,
) -> str | None:
    prefix = f"{_RESEARCH_INPUT_PREFIX}/{day}/{athlete_id}/"
    paths = artifact_store.list_paths(prefix=prefix, suffix=".json")
    if not paths:
        return None
    return sorted(paths)[-1]


def _build_research_record_from_activity(activity: dict[str, Any], athlete_id: int) -> dict[str, Any] | None:
    activity_id = _safe_int(activity.get("id"), 0)
    if activity_id <= 0:
        return None

    name = str(activity.get("name") or "")
    sport = str(activity.get("sport_type") or activity.get("type") or "")
    summary = f"{name} ({sport})".strip()
    metadata = _build_activity_metadata(activity, summary, athlete_id)
    text_for_embedding = f"{name} {sport} {summary}".strip()

    return {
        "_id": f"{athlete_id}_{activity_id}",
        "text": text_for_embedding,
        "_text": text_for_embedding,
        **metadata,
    }


def _load_research_records_for_day(
    artifact_store: ArtifactStore,
    athlete_id: int,
    day: str,
    *,
    allow_raw_fallback: bool,
) -> tuple[list[dict[str, Any]], str]:
    input_path = _latest_research_input_path_for_day(artifact_store, athlete_id, day)
    if input_path:
        payload = artifact_store.read_json(input_path)
        if isinstance(payload, dict):
            records = payload.get("records")
            if isinstance(records, list):
                valid_records = [item for item in records if isinstance(item, dict)]
                if valid_records:
                    return valid_records, "upsert_input"

    if not allow_raw_fallback:
        return [], "none"

    raw_records: list[dict[str, Any]] = []
    for path in _latest_activity_paths_for_day(artifact_store, athlete_id, day):
        activity = artifact_store.read_json(path)
        if not isinstance(activity, dict):
            continue
        if record := _build_research_record_from_activity(activity, athlete_id):
            raw_records.append(record)

    if raw_records:
        return raw_records, "raw_fallback"

    return [], "none"


def _average(values: list[float]) -> float | None:
    cleaned = [value for value in values if value > 0]
    if not cleaned:
        return None
    return sum(cleaned) / float(len(cleaned))


def _aggregate_research_metrics(
    target_records: list[dict[str, Any]],
    historical_records: list[dict[str, Any]],
) -> dict[str, Any]:
    def _distance_km(record: dict[str, Any]) -> float:
        return _safe_float(record.get("distance"), 0.0) / 1000.0

    def _moving_hours(record: dict[str, Any]) -> float:
        return _safe_float(record.get("moving_time"), 0.0) / 3600.0

    window_records = [*historical_records, *target_records]
    sport_distribution: dict[str, int] = {}
    for record in window_records:
        sport = str(record.get("sport_type") or record.get("type") or "unknown").strip().lower()
        if not sport:
            sport = "unknown"
        sport_distribution[sport] = sport_distribution.get(sport, 0) + 1

    target_hr = [_safe_float(record.get("average_heartrate"), 0.0) for record in target_records]
    target_watts = [_safe_float(record.get("average_watts"), 0.0) for record in target_records]
    window_hr = [_safe_float(record.get("average_heartrate"), 0.0) for record in window_records]

    return {
        "target_activities": len(target_records),
        "target_distance_km": round(sum(_distance_km(record) for record in target_records), 2),
        "target_moving_hours": round(sum(_moving_hours(record) for record in target_records), 2),
        "window_activities": len(window_records),
        "window_distance_km": round(sum(_distance_km(record) for record in window_records), 2),
        "window_moving_hours": round(sum(_moving_hours(record) for record in window_records), 2),
        "target_avg_heartrate": round(_average(target_hr), 1) if _average(target_hr) is not None else None,
        "target_avg_watts": round(_average(target_watts), 1) if _average(target_watts) is not None else None,
        "window_avg_heartrate": round(_average(window_hr), 1) if _average(window_hr) is not None else None,
        "sport_distribution": sport_distribution,
    }


def _read_existing_research(artifact_store: ArtifactStore, athlete_id: int) -> str | None:
    report_relative_path = f"wiki/{athlete_id}/research.md"
    return artifact_store.read_text(report_relative_path)


def _write_research_outputs(
    artifact_store: ArtifactStore,
    athlete_id: int,
    report_markdown: str,
) -> dict[str, str]:
    report_relative_path = f"wiki/{athlete_id}/research.md"
    report_uri = artifact_store.write_text(report_relative_path, report_markdown)
    return {
        "report_path": report_uri,
        "storage_mode": artifact_store.mode,
    }


def research_wiki_pipeline(
    athlete_ids_csv: str = "",
    target_date: str = "",
    window_days: int = 14,
    daily_run_id: str = "",
) -> dict[str, Any]:
    artifact_store = ArtifactStore()
    state_store = AthleteStateStore()

    day = _normalize_date(target_date)
    normalized_window_days = max(2, int(window_days))
    targets = _resolve_targets(state_store, athlete_ids_csv)
    run_id = uuid.uuid4().hex

    report: dict[str, Any] = {
        "stage": "research_wiki",
        "run_id": run_id,
        "daily_run_id": (daily_run_id or "").strip() or None,
        "target_date": day,
        "window_days": normalized_window_days,
        "athletes": [],
        "errors": [],
        "started_at": utc_now_iso(),
    }

    state_store.record_pipeline_run(
        run_id,
        {
            "run_id": run_id,
            "stage": "research_wiki",
            "status": "running",
            "target_date": day,
            "window_days": normalized_window_days,
            "daily_run_id": report["daily_run_id"],
            "started_at": report["started_at"],
        },
    )

    for target in targets:
        athlete_id = _safe_int(target.get("athlete_id"), 0)
        if athlete_id <= 0:
            continue

        target_records, target_source = _load_research_records_for_day(
            artifact_store,
            athlete_id,
            day,
            allow_raw_fallback=False,
        )
        if not target_records:
            report["athletes"].append(
                {
                    "athlete_id": athlete_id,
                    "status": "skipped",
                    "reason": "missing_upsert_input_for_target_date",
                }
            )
            continue

        historical_records: list[dict[str, Any]] = []
        raw_fallback_days = 0
        for candidate_day in _window_days(day, normalized_window_days):
            if candidate_day == day:
                continue
            records, source = _load_research_records_for_day(
                artifact_store,
                athlete_id,
                candidate_day,
                allow_raw_fallback=True,
            )
            if source == "raw_fallback" and records:
                raw_fallback_days += 1
            if records:
                historical_records.extend(records)

        metrics = _aggregate_research_metrics(target_records, historical_records)
        athlete_profile = target.get("profile") if isinstance(target.get("profile"), dict) else {}

        existing_report = _read_existing_research(artifact_store, athlete_id)

        try:
            research_result = run_deep_research_wiki_agent(
                athlete_id=athlete_id,
                target_date=day,
                window_days=normalized_window_days,
                target_records=target_records,
                historical_records=historical_records,
                metrics=metrics,
                athlete_profile=athlete_profile,
                existing_report=existing_report,
            )
        except Exception as exc:  # noqa: BLE001
            report["errors"].append({"athlete_id": athlete_id, "error": f"research_generation_failed: {exc}"})
            continue

        final_report = str(research_result.get("final_report") or "").strip()
        if not final_report:
            report["errors"].append({"athlete_id": athlete_id, "error": "empty_research_report"})
            continue

        storage_result = _write_research_outputs(
            artifact_store,
            athlete_id,
            final_report,
        )

        report["athletes"].append(
            {
                "athlete_id": athlete_id,
                "status": "compiled",
                "target_records": len(target_records),
                "historical_records": len(historical_records),
                "target_source": target_source,
                "raw_fallback_days": raw_fallback_days,
                "incremental_update": existing_report is not None,
                "evaluations": len(research_result.get("evaluations") or []),
                "report_path": storage_result["report_path"],
                "storage_mode": storage_result["storage_mode"],
            }
        )

    report["ok"] = not report["errors"]
    report["finished_at"] = utc_now_iso()
    all_skipped = bool(report["athletes"]) and all(
        a.get("status") == "skipped" for a in report["athletes"]
    )
    if all_skipped:
        report["status"] = "skipped"
    elif report["ok"]:
        report["status"] = "success"
    else:
        report["status"] = "partial_failure"
    state_store.record_pipeline_run(run_id, report)
    return report



# ─── Daily Pipeline ─────────────────────────────────────────────────────────

def run_daily_pipeline(
    athlete_ids_csv: str = "",
    target_date: str = "",
    lookback_days: int = 7,
    window_days: int = 14,
) -> dict[str, Any]:
    state_store = AthleteStateStore()
    run_id = uuid.uuid4().hex
    day = _normalize_date(target_date)

    started_payload = {
        "run_id": run_id,
        "target_date": day,
        "status": "running",
        "started_at": utc_now_iso(),
    }
    state_store.record_pipeline_run(run_id, started_payload)

    connector = StravaConnector(state_store)
    ingestion_report = run_ingestion(connector, athlete_ids_csv=athlete_ids_csv, lookback_days=lookback_days)

    pipeline_report = {
        "run_id": run_id,
        "target_date": day,
        "window_days": max(2, int(window_days)),
        "status": "success",
        "finished_at": utc_now_iso(),
        "steps": {
            "ingestion": ingestion_report,
        },
    }

    state_store.record_pipeline_run(run_id, pipeline_report)
    return pipeline_report


# ─── Pipeline wrappers (JSON string output for agent tools) ─────────────────

def run_ingestion_pipeline(athlete_ids_csv: str = "", lookback_days: int = 7) -> str:
    state_store = AthleteStateStore()
    connector = StravaConnector(state_store)
    return json.dumps(
        run_ingestion(connector, athlete_ids_csv=athlete_ids_csv, lookback_days=lookback_days),
        ensure_ascii=False,
    )


def run_research_wiki_pipeline(
    athlete_ids_csv: str = "",
    target_date: str = "",
    window_days: int = 14,
    daily_run_id: str = "",
) -> str:
    return json.dumps(
        research_wiki_pipeline(
            athlete_ids_csv=athlete_ids_csv,
            target_date=target_date,
            window_days=window_days,
            daily_run_id=daily_run_id,
        ),
        ensure_ascii=False,
    )


def run_daily_orchestration_pipeline(
    athlete_ids_csv: str = "",
    target_date: str = "",
    lookback_days: int = 7,
    window_days: int = 14,
) -> str:
    return json.dumps(
        run_daily_pipeline(
            athlete_ids_csv=athlete_ids_csv,
            target_date=target_date,
            lookback_days=lookback_days,
            window_days=window_days,
        ),
        ensure_ascii=False,
    )
