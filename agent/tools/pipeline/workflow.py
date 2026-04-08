from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

from .storage_backend import ArtifactStore, AthleteStateStore, utc_now_iso

try:
    from pinecone import Pinecone
except Exception:  # noqa: BLE001
    Pinecone = None


_STRAVA_API_BASE_URL = "https://www.strava.com/api/v3"
_MAX_SYNC_PAGES = 10
_PER_PAGE = 100


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


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _format_duration(seconds: int) -> str:
    total = max(0, int(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    remaining = total % 60
    return f"{hours:02d}:{minutes:02d}:{remaining:02d}"


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


def _tokenize(text: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", text.lower()) if token}


def _strava_get(access_token: str, path: str, params: dict[str, Any] | None = None) -> Any:
    response = requests.get(
        url=f"{_STRAVA_API_BASE_URL}{path}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        params={key: value for key, value in (params or {}).items() if value is not None},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


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


def _activity_report(activity: dict[str, Any]) -> str:
    activity_id = _safe_int(activity.get("id"), 0)
    name = str(activity.get("name") or f"Activity {activity_id}")
    sport_type = str(activity.get("sport_type") or activity.get("type") or "unknown")

    distance_km = _safe_float(activity.get("distance"), 0.0) / 1000.0
    moving_time_s = _safe_int(activity.get("moving_time"), 0)
    elapsed_time_s = _safe_int(activity.get("elapsed_time"), 0)
    elevation_m = _safe_float(activity.get("total_elevation_gain"), 0.0)

    avg_speed = _safe_float(activity.get("average_speed"), 0.0) * 3.6
    max_speed = _safe_float(activity.get("max_speed"), 0.0) * 3.6
    avg_hr = _safe_float(activity.get("average_heartrate"), 0.0)
    max_hr = _safe_float(activity.get("max_heartrate"), 0.0)

    return "\n".join(
        [
            f"# Activity Report: {name}",
            "",
            f"- activity_id: {activity_id}",
            f"- sport_type: {sport_type}",
            f"- start_date_local: {activity.get('start_date_local')}",
            f"- distance_km: {distance_km:.2f}",
            f"- moving_time: {_format_duration(moving_time_s)}",
            f"- elapsed_time: {_format_duration(elapsed_time_s)}",
            f"- elevation_gain_m: {elevation_m:.1f}",
            f"- avg_speed_kmh: {avg_speed:.2f}",
            f"- max_speed_kmh: {max_speed:.2f}",
            f"- average_heartrate: {avg_hr:.1f}",
            f"- max_heartrate: {max_hr:.1f}",
            "",
            "## Signals",
            f"- Intensity marker: {'high' if avg_hr >= 150 else 'moderate' if avg_hr >= 130 else 'low'}",
            f"- Endurance marker: {'long' if moving_time_s >= 7200 else 'medium' if moving_time_s >= 3600 else 'short'}",
        ]
    )


def _aggregate_daily_metrics(activities: list[dict[str, Any]]) -> dict[str, Any]:
    total_activities = len(activities)
    total_distance_km = sum(_safe_float(item.get("distance"), 0.0) for item in activities) / 1000.0
    total_moving_time_s = sum(_safe_int(item.get("moving_time"), 0) for item in activities)
    total_elevation_m = sum(_safe_float(item.get("total_elevation_gain"), 0.0) for item in activities)

    hr_values = [
        _safe_float(item.get("average_heartrate"), 0.0)
        for item in activities
        if _safe_float(item.get("average_heartrate"), 0.0) > 0
    ]

    return {
        "total_activities": total_activities,
        "total_distance_km": round(total_distance_km, 2),
        "total_moving_time_h": round(total_moving_time_s / 3600.0, 2),
        "total_moving_time_s": total_moving_time_s,
        "total_elevation_gain_m": round(total_elevation_m, 1),
        "average_heartrate": round(sum(hr_values) / len(hr_values), 1) if hr_values else None,
    }


def _daily_summary_markdown(
    athlete_id: int,
    day: str,
    activities: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> str:
    lines = [
        f"# Daily Summary {day}",
        "",
        f"- athlete_id: {athlete_id}",
        f"- activities: {metrics.get('total_activities', 0)}",
        f"- distance_km: {metrics.get('total_distance_km', 0.0):.2f}",
        f"- moving_time_h: {metrics.get('total_moving_time_h', 0.0):.2f}",
        f"- elevation_gain_m: {metrics.get('total_elevation_gain_m', 0.0):.1f}",
    ]

    if metrics.get("average_heartrate") is not None:
        lines.append(f"- average_heartrate: {metrics.get('average_heartrate'):.1f}")

    lines.extend(["", "## Activities"])
    for activity in activities:
        activity_id = _safe_int(activity.get("id"), 0)
        name = str(activity.get("name") or f"Activity {activity_id}")
        sport = str(activity.get("sport_type") or activity.get("type") or "unknown")
        distance_km = _safe_float(activity.get("distance"), 0.0) / 1000.0
        lines.append(f"- {name} ({sport}) - {distance_km:.2f} km")

    return "\n".join(lines)


def _build_trend_insight(metrics_by_day: list[dict[str, Any]]) -> dict[str, Any]:
    if not metrics_by_day:
        return {
            "trend": "insufficient_data",
            "fatigue": "unknown",
            "early_distance_km": 0.0,
            "late_distance_km": 0.0,
            "training_days": 0,
        }

    half = max(1, len(metrics_by_day) // 2)
    early = metrics_by_day[:half]
    late = metrics_by_day[half:]

    early_distance = sum(_safe_float(item.get("total_distance_km"), 0.0) for item in early)
    late_distance = sum(_safe_float(item.get("total_distance_km"), 0.0) for item in late)

    early_time = sum(_safe_float(item.get("total_moving_time_h"), 0.0) for item in early)
    late_time = sum(_safe_float(item.get("total_moving_time_h"), 0.0) for item in late)

    if late_distance > early_distance * 1.08:
        trend = "improving_endurance"
    elif late_distance < early_distance * 0.92:
        trend = "declining_load"
    else:
        trend = "stable"

    training_days = sum(1 for item in metrics_by_day if _safe_int(item.get("total_activities"), 0) > 0)

    if training_days >= len(metrics_by_day) - 1 and late_time > early_time * 1.20:
        fatigue = "high"
    elif late_time > early_time * 1.08:
        fatigue = "moderate"
    else:
        fatigue = "low"

    return {
        "trend": trend,
        "fatigue": fatigue,
        "early_distance_km": round(early_distance, 2),
        "late_distance_km": round(late_distance, 2),
        "training_days": training_days,
    }


def _insight_markdown(athlete_id: int, end_date: str, insight: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"# Performance Insight {end_date}",
            "",
            f"- athlete_id: {athlete_id}",
            f"- trend: {insight.get('trend')}",
            f"- fatigue: {insight.get('fatigue')}",
            f"- early_distance_km: {insight.get('early_distance_km', 0.0):.2f}",
            f"- late_distance_km: {insight.get('late_distance_km', 0.0):.2f}",
            f"- training_days: {insight.get('training_days', 0)}",
            "",
            "## Interpretation",
            (
                "- Endurance is trending up."
                if insight.get("trend") == "improving_endurance"
                else "- Endurance trend is flat or down."
            ),
            (
                "- Fatigue accumulation signal detected."
                if insight.get("fatigue") in {"high", "moderate"}
                else "- Fatigue signal is controlled."
            ),
        ]
    )


def _date_range(end_date: str, window_days: int) -> list[str]:
    end = _parse_date(end_date)
    return [
        (end - timedelta(days=offset)).isoformat()
        for offset in range(max(0, window_days - 1), -1, -1)
    ]


def _pinecone_context() -> tuple[Any, Any, str, str] | None:
    api_key = (os.environ.get("PINECONE_API_KEY") or "").strip()
    index_name = (os.environ.get("PINECONE_INDEX_NAME") or "").strip()
    embedding_model = (os.environ.get("PINECONE_EMBEDDING_MODEL") or "").strip()
    namespace = (os.environ.get("PINECONE_NAMESPACE") or "strava-knowledge").strip()

    if not api_key or not index_name or not embedding_model or Pinecone is None:
        return None

    try:
        client = Pinecone(api_key=api_key)
        index = client.Index(index_name)
        return client, index, embedding_model, namespace
    except Exception:  # noqa: BLE001
        return None


def _chunk_text(text: str, max_chars: int = 900) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []

    paragraphs = [segment.strip() for segment in stripped.split("\n\n") if segment.strip()]
    chunks: list[str] = []
    buffer = ""

    for paragraph in paragraphs:
        if not buffer:
            if len(paragraph) <= max_chars:
                buffer = paragraph
            else:
                for start in range(0, len(paragraph), max_chars):
                    chunks.append(paragraph[start : start + max_chars])
            continue

        if len(buffer) + 2 + len(paragraph) <= max_chars:
            buffer = f"{buffer}\n\n{paragraph}"
        else:
            chunks.append(buffer)
            if len(paragraph) <= max_chars:
                buffer = paragraph
            else:
                for start in range(0, len(paragraph), max_chars):
                    chunks.append(paragraph[start : start + max_chars])
                buffer = ""

    if buffer:
        chunks.append(buffer)

    return chunks


def run_strava_ingestion(
    athlete_ids_csv: str = "",
    lookback_days: int = 7,
    max_pages: int = _MAX_SYNC_PAGES,
    per_page: int = _PER_PAGE,
) -> dict[str, Any]:
    state_store = AthleteStateStore()
    artifact_store = ArtifactStore()

    targets = _resolve_targets(state_store, athlete_ids_csv)
    now_epoch = int(datetime.now(timezone.utc).timestamp())
    lookback_seconds = max(1, int(lookback_days)) * 24 * 60 * 60

    run_report: dict[str, Any] = {
        "stage": "ingestion",
        "started_at": utc_now_iso(),
        "store_mode": artifact_store.mode,
        "state_mode": state_store.mode,
        "athletes": [],
        "errors": [],
    }

    for target in targets:
        athlete_id = _safe_int(target.get("athlete_id"), 0)
        access_token = str(target.get("access_token") or "").strip()

        if athlete_id <= 0 or not access_token:
            run_report["errors"].append(
                {
                    "athlete_id": athlete_id,
                    "error": "missing_access_token_or_athlete_id",
                }
            )
            continue

        previous_sync = state_store.get_last_sync_epoch(athlete_id)
        after_epoch = max(previous_sync or 0, now_epoch - lookback_seconds)
        sync_stamp = _compact_timestamp()

        athlete_report: dict[str, Any] = {
            "athlete_id": athlete_id,
            "after_epoch": after_epoch,
            "stored_activities": 0,
            "pages": 0,
            "manifest_path": None,
        }

        try:
            profile_payload = _strava_get(access_token, "/athlete")
            if isinstance(profile_payload, dict):
                state_store.upsert_tokens(
                    athlete_id,
                    {
                        "access_token": access_token,
                        "refresh_token": target.get("refresh_token"),
                        "expires_at": target.get("expires_at"),
                        "scope": target.get("scope"),
                        "athlete": profile_payload,
                    },
                )
        except requests.RequestException:
            pass

        try:
            for page in range(1, max(1, int(max_pages)) + 1):
                payload = _strava_get(
                    access_token,
                    "/athlete/activities",
                    params={
                        "after": after_epoch,
                        "page": page,
                        "per_page": max(1, min(int(per_page), 200)),
                    },
                )

                athlete_report["pages"] = page
                if not isinstance(payload, list) or not payload:
                    break

                for activity in payload:
                    if not isinstance(activity, dict):
                        continue

                    activity_id = _safe_int(activity.get("id"), 0)
                    if activity_id <= 0:
                        continue

                    activity_day = _activity_day(activity)
                    relative_path = (
                        f"raw/athletes/{athlete_id}/activities/{activity_day}/{activity_id}_{sync_stamp}.json"
                    )
                    artifact_store.write_json(relative_path, activity)
                    athlete_report["stored_activities"] += 1

                if len(payload) < per_page:
                    break

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
                details={
                    "stored_activities": athlete_report["stored_activities"],
                    "pages": athlete_report["pages"],
                },
            )
            run_report["athletes"].append(athlete_report)
        except requests.HTTPError as exc:
            state_store.update_sync_state(
                athlete_id,
                last_sync_epoch=now_epoch,
                status="failed",
                details={"error": str(exc)},
            )
            run_report["errors"].append(
                {
                    "athlete_id": athlete_id,
                    "error": str(exc),
                }
            )
        except requests.RequestException as exc:
            state_store.update_sync_state(
                athlete_id,
                last_sync_epoch=now_epoch,
                status="failed",
                details={"error": str(exc)},
            )
            run_report["errors"].append(
                {
                    "athlete_id": athlete_id,
                    "error": str(exc),
                }
            )

    run_report["finished_at"] = utc_now_iso()
    run_report["ok"] = not run_report["errors"]
    return run_report


def run_activity_analysis(
    athlete_ids_csv: str = "",
    target_date: str = "",
) -> dict[str, Any]:
    artifact_store = ArtifactStore()
    state_store = AthleteStateStore()

    day = _normalize_date(target_date)
    targets = _resolve_targets(state_store, athlete_ids_csv)

    report: dict[str, Any] = {
        "stage": "activity_analysis",
        "target_date": day,
        "athletes": [],
        "errors": [],
    }

    for target in targets:
        athlete_id = _safe_int(target.get("athlete_id"), 0)
        if athlete_id <= 0:
            continue

        paths = _latest_activity_paths_for_day(artifact_store, athlete_id, day)
        stored_reports = 0

        for path in paths:
            activity_payload = artifact_store.read_json(path)
            if not isinstance(activity_payload, dict):
                continue

            activity_id = _safe_int(activity_payload.get("id"), 0)
            if activity_id <= 0:
                continue

            markdown = _activity_report(activity_payload)
            report_path = f"processed/athletes/{athlete_id}/activity_reports/{day}/{activity_id}.md"
            artifact_store.write_text(report_path, markdown)
            stored_reports += 1

        report["athletes"].append(
            {
                "athlete_id": athlete_id,
                "activity_reports": stored_reports,
            }
        )

    report["ok"] = True
    return report


def run_daily_summary(
    athlete_ids_csv: str = "",
    target_date: str = "",
) -> dict[str, Any]:
    artifact_store = ArtifactStore()
    state_store = AthleteStateStore()

    day = _normalize_date(target_date)
    targets = _resolve_targets(state_store, athlete_ids_csv)

    report: dict[str, Any] = {
        "stage": "daily_summary",
        "target_date": day,
        "athletes": [],
        "errors": [],
    }

    for target in targets:
        athlete_id = _safe_int(target.get("athlete_id"), 0)
        if athlete_id <= 0:
            continue

        paths = _latest_activity_paths_for_day(artifact_store, athlete_id, day)
        activities: list[dict[str, Any]] = []
        for path in paths:
            payload = artifact_store.read_json(path)
            if isinstance(payload, dict):
                activities.append(payload)

        metrics = _aggregate_daily_metrics(activities)
        markdown = _daily_summary_markdown(athlete_id, day, activities, metrics)

        markdown_path = f"processed/athletes/{athlete_id}/daily_summaries/{day}.md"
        json_path = f"processed/athletes/{athlete_id}/daily_summaries/{day}.json"

        artifact_store.write_text(markdown_path, markdown)
        artifact_store.write_json(
            json_path,
            {
                "athlete_id": athlete_id,
                "date": day,
                "metrics": metrics,
                "activity_count": len(activities),
                "generated_at": utc_now_iso(),
            },
        )

        report["athletes"].append(
            {
                "athlete_id": athlete_id,
                "activity_count": len(activities),
                "summary_markdown_path": markdown_path,
                "summary_json_path": json_path,
            }
        )

    report["ok"] = True
    return report


def run_performance_insight(
    athlete_ids_csv: str = "",
    target_date: str = "",
    window_days: int = 14,
) -> dict[str, Any]:
    artifact_store = ArtifactStore()
    state_store = AthleteStateStore()

    end_date = _normalize_date(target_date)
    window = max(2, int(window_days))
    date_window = _date_range(end_date, window)
    targets = _resolve_targets(state_store, athlete_ids_csv)

    report: dict[str, Any] = {
        "stage": "performance_insight",
        "target_date": end_date,
        "window_days": window,
        "athletes": [],
        "errors": [],
    }

    for target in targets:
        athlete_id = _safe_int(target.get("athlete_id"), 0)
        if athlete_id <= 0:
            continue

        metrics_by_day: list[dict[str, Any]] = []
        for day in date_window:
            summary_path = f"processed/athletes/{athlete_id}/daily_summaries/{day}.json"
            summary_payload = artifact_store.read_json(summary_path)
            if not isinstance(summary_payload, dict):
                continue
            metrics = summary_payload.get("metrics")
            if not isinstance(metrics, dict):
                continue
            metrics_by_day.append(metrics)

        insight = _build_trend_insight(metrics_by_day)
        markdown = _insight_markdown(athlete_id, end_date, insight)

        markdown_path = f"processed/athletes/{athlete_id}/insights/{end_date}.md"
        json_path = f"processed/athletes/{athlete_id}/insights/{end_date}.json"

        artifact_store.write_text(markdown_path, markdown)
        artifact_store.write_json(
            json_path,
            {
                "athlete_id": athlete_id,
                "date": end_date,
                "window_days": window,
                "insight": insight,
                "generated_at": utc_now_iso(),
            },
        )

        report["athletes"].append(
            {
                "athlete_id": athlete_id,
                "insight_markdown_path": markdown_path,
                "insight_json_path": json_path,
                "trend": insight.get("trend"),
                "fatigue": insight.get("fatigue"),
            }
        )

    report["ok"] = True
    return report


def run_wiki_builder(
    athlete_ids_csv: str = "",
    target_date: str = "",
) -> dict[str, Any]:
    artifact_store = ArtifactStore()
    state_store = AthleteStateStore()

    day = _normalize_date(target_date)
    targets = _resolve_targets(state_store, athlete_ids_csv)

    report: dict[str, Any] = {
        "stage": "wiki_builder",
        "target_date": day,
        "athletes": [],
        "errors": [],
    }

    for target in targets:
        athlete_id = _safe_int(target.get("athlete_id"), 0)
        if athlete_id <= 0:
            continue

        athlete_data = state_store.get_athlete(athlete_id) or {}
        profile = athlete_data.get("profile") if isinstance(athlete_data.get("profile"), dict) else {}

        summary_path = f"processed/athletes/{athlete_id}/daily_summaries/{day}.md"
        summary_text = artifact_store.read_text(summary_path) or "# Daily Summary\n\nNo summary available yet."

        insight_path = f"processed/athletes/{athlete_id}/insights/{day}.md"
        insight_text = artifact_store.read_text(insight_path) or "# Performance Insight\n\nNo insight available yet."

        profile_md = "\n".join(
            [
                "# Athlete Profile",
                "",
                f"- athlete_id: {athlete_id}",
                f"- firstname: {profile.get('firstname', 'unknown')}",
                f"- lastname: {profile.get('lastname', 'unknown')}",
                f"- city: {profile.get('city', 'unknown')}",
                f"- country: {profile.get('country', 'unknown')}",
                f"- last_sync_epoch: {athlete_data.get('last_sync_epoch', 'unknown')}",
                f"- updated_at: {utc_now_iso()}",
            ]
        )

        profile_doc_path = f"wiki/athletes/{athlete_id}/profile.md"
        summary_doc_path = f"wiki/athletes/{athlete_id}/summaries/{day}.md"
        trends_doc_path = f"wiki/athletes/{athlete_id}/trends/weekly.md"
        fatigue_doc_path = f"wiki/athletes/{athlete_id}/insights/fatigue.md"

        artifact_store.write_text(profile_doc_path, profile_md)
        artifact_store.write_text(summary_doc_path, summary_text)

        recent_days = _date_range(day, 7)
        weekly_distance = 0.0
        weekly_time = 0.0
        for recent_day in recent_days:
            summary_json_path = f"processed/athletes/{athlete_id}/daily_summaries/{recent_day}.json"
            summary_json = artifact_store.read_json(summary_json_path)
            if not isinstance(summary_json, dict):
                continue
            metrics = summary_json.get("metrics")
            if not isinstance(metrics, dict):
                continue
            weekly_distance += _safe_float(metrics.get("total_distance_km"), 0.0)
            weekly_time += _safe_float(metrics.get("total_moving_time_h"), 0.0)

        weekly_md = "\n".join(
            [
                "# Weekly Trends",
                "",
                f"- reference_date: {day}",
                f"- distance_km_7d: {weekly_distance:.2f}",
                f"- moving_time_h_7d: {weekly_time:.2f}",
                "",
                "This file is updated as a snapshot on every daily pipeline run.",
            ]
        )

        artifact_store.write_text(trends_doc_path, weekly_md)
        artifact_store.write_text(fatigue_doc_path, insight_text)

        snapshot_id = f"{day}-{_compact_timestamp()}"
        snapshot_manifest_path = f"wiki/athletes/{athlete_id}/snapshots/{snapshot_id}.json"
        artifact_store.write_json(
            snapshot_manifest_path,
            {
                "athlete_id": athlete_id,
                "snapshot_id": snapshot_id,
                "date": day,
                "files": {
                    "profile": profile_doc_path,
                    "summary": summary_doc_path,
                    "trends": trends_doc_path,
                    "fatigue": fatigue_doc_path,
                },
                "created_at": utc_now_iso(),
            },
        )

        report["athletes"].append(
            {
                "athlete_id": athlete_id,
                "snapshot_id": snapshot_id,
                "manifest_path": snapshot_manifest_path,
            }
        )

    report["ok"] = True
    return report


def _upsert_documents_pinecone(
    athlete_id: int,
    target_date: str,
    documents: list[dict[str, str]],
) -> tuple[int, str]:
    context = _pinecone_context()
    if context is None:
        return 0, ""

    client, index, model, namespace = context
    del client

    texts: list[str] = []
    vector_inputs: list[tuple[str, str, str, str]] = []
    snapshot_id = f"{target_date}-{_compact_timestamp()}"

    for doc in documents:
        doc_type = doc.get("type", "document")
        source_path = doc.get("source_path", "")
        content = doc.get("content", "")
        chunks = _chunk_text(content)

        for chunk_index, chunk in enumerate(chunks):
            digest = hashlib.sha1(
                f"{athlete_id}:{doc_type}:{source_path}:{chunk_index}:{chunk}".encode("utf-8")
            ).hexdigest()[:24]
            vector_id = f"{athlete_id}:{doc_type}:{target_date}:{digest}"
            vector_inputs.append((vector_id, doc_type, source_path, chunk))
            texts.append(chunk)

    if not texts:
        return 0, namespace

    embedding_response = index = None
    context = _pinecone_context()
    if context is None:
        return 0, ""
    client, index, model, namespace = context

    embedding_response = client.inference.embed(
        model=model,
        inputs=texts,
        parameters={"input_type": "passage", "truncate": "END"},
    )
    embeddings = [item.values for item in embedding_response.data]

    vectors: list[dict[str, Any]] = []
    for idx, vector_meta in enumerate(vector_inputs):
        vector_id, doc_type, source_path, chunk_text = vector_meta
        vectors.append(
            {
                "id": vector_id,
                "values": embeddings[idx],
                "metadata": {
                    "athlete_id": str(athlete_id),
                    "date": target_date,
                    "type": doc_type,
                    "source_path": source_path,
                    "snapshot_id": snapshot_id,
                    "text": chunk_text[:1800],
                },
            }
        )

    index.upsert(vectors=vectors, namespace=namespace)
    return len(vectors), namespace


def run_embedding_index(
    athlete_ids_csv: str = "",
    target_date: str = "",
    force_reindex: bool = False,
) -> dict[str, Any]:
    artifact_store = ArtifactStore()
    state_store = AthleteStateStore()

    day = _normalize_date(target_date)
    targets = _resolve_targets(state_store, athlete_ids_csv)

    report: dict[str, Any] = {
        "stage": "embedding",
        "target_date": day,
        "athletes": [],
        "errors": [],
    }

    for target in targets:
        athlete_id = _safe_int(target.get("athlete_id"), 0)
        if athlete_id <= 0:
            continue

        last_indexed_date = state_store.get_last_indexed_date(athlete_id)
        if not force_reindex and last_indexed_date == day:
            report["athletes"].append(
                {
                    "athlete_id": athlete_id,
                    "status": "skipped",
                    "reason": "already_indexed_for_date",
                }
            )
            continue

        doc_paths = [
            ("profile", f"wiki/athletes/{athlete_id}/profile.md"),
            ("daily_summary", f"wiki/athletes/{athlete_id}/summaries/{day}.md"),
            ("weekly_trend", f"wiki/athletes/{athlete_id}/trends/weekly.md"),
            ("fatigue_insight", f"wiki/athletes/{athlete_id}/insights/fatigue.md"),
        ]

        documents: list[dict[str, str]] = []
        for doc_type, path in doc_paths:
            text = artifact_store.read_text(path)
            if not isinstance(text, str) or not text.strip():
                continue
            documents.append(
                {
                    "type": doc_type,
                    "source_path": path,
                    "content": text,
                }
            )

        if not documents:
            report["athletes"].append(
                {
                    "athlete_id": athlete_id,
                    "status": "skipped",
                    "reason": "no_documents",
                }
            )
            continue

        upserted_vectors, namespace = _upsert_documents_pinecone(athlete_id, day, documents)

        if upserted_vectors == 0:
            local_path = f"index/athletes/{athlete_id}/{day}.json"
            artifact_store.write_json(
                local_path,
                {
                    "athlete_id": athlete_id,
                    "date": day,
                    "documents": documents,
                    "created_at": utc_now_iso(),
                },
            )
            report["athletes"].append(
                {
                    "athlete_id": athlete_id,
                    "status": "local_fallback",
                    "vectors_upserted": 0,
                    "local_index_path": local_path,
                }
            )
        else:
            report["athletes"].append(
                {
                    "athlete_id": athlete_id,
                    "status": "indexed",
                    "vectors_upserted": upserted_vectors,
                    "namespace": namespace,
                }
            )

        state_store.set_last_indexed_date(athlete_id, day)

    report["ok"] = True
    return report


def _query_local_index(
    artifact_store: ArtifactStore,
    athlete_id: int,
    question: str,
    top_k: int,
) -> list[dict[str, Any]]:
    paths = artifact_store.list_paths(f"index/athletes/{athlete_id}/", suffix=".json")
    tokens = _tokenize(question)

    hits: list[dict[str, Any]] = []
    for path in paths:
        payload = artifact_store.read_json(path)
        if not isinstance(payload, dict):
            continue
        documents = payload.get("documents")
        if not isinstance(documents, list):
            continue

        for doc in documents:
            if not isinstance(doc, dict):
                continue
            content = str(doc.get("content") or "")
            overlap = len(tokens.intersection(_tokenize(content)))
            if overlap <= 0:
                continue
            hits.append(
                {
                    "score": float(overlap),
                    "type": str(doc.get("type") or "document"),
                    "source_path": str(doc.get("source_path") or path),
                    "text": content[:1500],
                }
            )

    hits.sort(key=lambda item: item.get("score", 0.0), reverse=True)
    return hits[: max(1, int(top_k))]


def run_query_layer(
    question: str,
    athlete_id: int,
    top_k: int = 5,
    target_date: str = "",
) -> dict[str, Any]:
    normalized_top_k = max(1, int(top_k))
    context = _pinecone_context()
    normalized_target_date = target_date.strip() if isinstance(target_date, str) else ""

    if context is None:
        artifact_store = ArtifactStore()
        local_hits = _query_local_index(artifact_store, athlete_id, question, normalized_top_k)
        context_text = "\n\n".join(
            [
                f"[{hit.get('type')}] {hit.get('text', '')}"
                for hit in local_hits
            ]
        )
        return {
            "mode": "local",
            "athlete_id": athlete_id,
            "hits": local_hits,
            "context": context_text,
            "target_date": _normalize_date(normalized_target_date),
        }

    client, index, model, namespace = context
    del index

    date_filter = _normalize_date(normalized_target_date) if normalized_target_date else ""
    metadata_filter: dict[str, Any] = {"athlete_id": {"$eq": str(athlete_id)}}
    if date_filter:
        metadata_filter["date"] = {"$eq": date_filter}

    query_embedding_response = client.inference.embed(
        model=model,
        inputs=[question],
        parameters={"input_type": "query", "truncate": "END"},
    )
    query_vector = query_embedding_response.data[0].values

    context = _pinecone_context()
    if context is None:
        return {
            "mode": "none",
            "athlete_id": athlete_id,
            "hits": [],
            "context": "",
            "target_date": date_filter,
        }

    _, index, _, namespace = context

    try:
        query_response = index.query(
            vector=query_vector,
            top_k=normalized_top_k,
            include_metadata=True,
            namespace=namespace,
            filter=metadata_filter,
        )
    except TypeError:
        query_response = index.query(
            queries=[query_vector],
            top_k=normalized_top_k,
            include_metadata=True,
            namespace=namespace,
            filter=metadata_filter,
        )

    matches = getattr(query_response, "matches", None)
    if matches is None and isinstance(query_response, dict):
        matches = query_response.get("matches")
    if matches is None:
        matches = []

    hits: list[dict[str, Any]] = []
    for match in matches:
        metadata = getattr(match, "metadata", None)
        if metadata is None and isinstance(match, dict):
            metadata = match.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        score = getattr(match, "score", None)
        if score is None and isinstance(match, dict):
            score = match.get("score")

        text = str(metadata.get("text") or "")
        hits.append(
            {
                "score": _safe_float(score, 0.0),
                "type": str(metadata.get("type") or "document"),
                "source_path": str(metadata.get("source_path") or ""),
                "date": str(metadata.get("date") or ""),
                "text": text,
            }
        )

    context_text = "\n\n".join(
        [
            f"[{item.get('type')}] {item.get('text', '')}"
            for item in hits
            if str(item.get("text", "")).strip()
        ]
    )

    return {
        "mode": "pinecone",
        "athlete_id": athlete_id,
        "hits": hits,
        "context": context_text,
        "target_date": date_filter,
        "namespace": namespace,
    }


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

    ingestion_report = run_strava_ingestion(athlete_ids_csv=athlete_ids_csv, lookback_days=lookback_days)
    analysis_report = run_activity_analysis(athlete_ids_csv=athlete_ids_csv, target_date=day)
    summary_report = run_daily_summary(athlete_ids_csv=athlete_ids_csv, target_date=day)
    insight_report = run_performance_insight(
        athlete_ids_csv=athlete_ids_csv,
        target_date=day,
        window_days=window_days,
    )
    wiki_report = run_wiki_builder(athlete_ids_csv=athlete_ids_csv, target_date=day)
    embedding_report = run_embedding_index(athlete_ids_csv=athlete_ids_csv, target_date=day)

    pipeline_report = {
        "run_id": run_id,
        "target_date": day,
        "status": "success",
        "finished_at": utc_now_iso(),
        "steps": {
            "ingestion": ingestion_report,
            "activity_analysis": analysis_report,
            "daily_summary": summary_report,
            "performance_insight": insight_report,
            "wiki_builder": wiki_report,
            "embedding": embedding_report,
        },
    }

    state_store.record_pipeline_run(run_id, pipeline_report)
    return pipeline_report


def run_ingestion_pipeline(athlete_ids_csv: str = "", lookback_days: int = 7) -> str:
    return json.dumps(
        run_strava_ingestion(athlete_ids_csv=athlete_ids_csv, lookback_days=lookback_days),
        ensure_ascii=False,
    )


def run_activity_analysis_pipeline(athlete_ids_csv: str = "", target_date: str = "") -> str:
    return json.dumps(
        run_activity_analysis(athlete_ids_csv=athlete_ids_csv, target_date=target_date),
        ensure_ascii=False,
    )


def run_daily_summary_pipeline(athlete_ids_csv: str = "", target_date: str = "") -> str:
    return json.dumps(
        run_daily_summary(athlete_ids_csv=athlete_ids_csv, target_date=target_date),
        ensure_ascii=False,
    )


def run_performance_insight_pipeline(
    athlete_ids_csv: str = "",
    target_date: str = "",
    window_days: int = 14,
) -> str:
    return json.dumps(
        run_performance_insight(
            athlete_ids_csv=athlete_ids_csv,
            target_date=target_date,
            window_days=window_days,
        ),
        ensure_ascii=False,
    )


def run_wiki_builder_pipeline(athlete_ids_csv: str = "", target_date: str = "") -> str:
    return json.dumps(
        run_wiki_builder(athlete_ids_csv=athlete_ids_csv, target_date=target_date),
        ensure_ascii=False,
    )


def run_embedding_pipeline(
    athlete_ids_csv: str = "",
    target_date: str = "",
    force_reindex: bool = False,
) -> str:
    return json.dumps(
        run_embedding_index(
            athlete_ids_csv=athlete_ids_csv,
            target_date=target_date,
            force_reindex=force_reindex,
        ),
        ensure_ascii=False,
    )


def run_query_pipeline(question: str, athlete_id: int = 0, top_k: int = 5, target_date: str = "") -> str:
    resolved_athlete_id = _safe_int(athlete_id, 0)
    if resolved_athlete_id <= 0:
        return json.dumps(
            {
                "mode": "error",
                "error": "athlete_id_required",
                "context": "",
                "hits": [],
            },
            ensure_ascii=False,
        )

    return json.dumps(
        run_query_layer(
            question=question,
            athlete_id=resolved_athlete_id,
            top_k=top_k,
            target_date=target_date,
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
