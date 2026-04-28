from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from .connectors.base import DataConnector
from .connectors.strava import StravaConnector
from .storage_backend import ArtifactStore, AthleteStateStore, utc_now_iso
from .wiki_llm import (
    bootstrap_wiki,
    format_log_entry,
    generate_index,
    triage_activity,
    update_page,
    update_page_with_graph,
)
from .wiki_pages import PAGE_SLUGS
from .knowledge_graph import extract_graph as _extract_graph_from_content
from .wiki_vector_index import index_graph_page as _vector_index_graph_page
from .wiki_vector_index import index_page as _vector_index_page
from .wiki_vector_index import index_pages as _vector_index_pages
from .wiki_vector_index import retrieve_graph_context as _retrieve_graph_context

_RESEARCH_INPUT_PREFIX = "pipeline/research-wiki-input"
_LATEST_ACTIVITIES_LIMIT = 10


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

def _existing_activity_ids_in_bucket(artifact_store: ArtifactStore) -> set[str]:
    """Devuelve el conjunto de IDs de actividad ya presentes en el bucket.

    Cada blob bajo ``pipeline/research-wiki-input/`` se nombra con el ID
    de la actividad de Strava, por lo que el basename del path equivale al
    identificador que queremos comprobar.
    """
    existing: set[str] = set()
    prefix = f"{_RESEARCH_INPUT_PREFIX}/"
    for path in artifact_store.list_paths(prefix=prefix):
        normalized = str(path).strip()
        if not normalized:
            continue
        # Strip the prefix and any extension to recover just the activity id.
        tail = normalized.split("/")[-1]
        if not tail:
            continue
        activity_id, _, _ = tail.partition(".")
        if activity_id:
            existing.add(activity_id)
    return existing


def _build_activity_firestore_payload(
    activity: dict[str, Any],
    athlete_id: int,
) -> dict[str, Any]:
    """Extrae los campos básicos de la actividad para mostrar en frontend."""
    payload: dict[str, Any] = {
        "activity_id": _safe_int(activity.get("id"), 0),
        "athlete_id": athlete_id,
    }
    flat_fields = [
        "name",
        "sport_type",
        "type",
        "distance",
        "moving_time",
        "elapsed_time",
        "total_elevation_gain",
        "average_speed",
        "max_speed",
        "average_heartrate",
        "max_heartrate",
        "average_watts",
        "kilojoules",
        "start_date",
        "start_date_local",
        "timezone",
        "workout_type",
        "device_name",
        "has_heartrate",
        "elev_high",
        "elev_low",
        "pr_count",
        "achievement_count",
        "kudos_count",
        "athlete_count",
        "gear_id",
        "trainer",
        "commute",
        "manual",
    ]
    for field in flat_fields:
        value = activity.get(field)
        if value is not None:
            payload[field] = value
    return payload


def run_ingestion(
    connector: DataConnector,
    athlete_ids_csv: str = "",
    latest_limit: int = _LATEST_ACTIVITIES_LIMIT,
) -> dict[str, Any]:
    """Pipeline de ingesta por actividad (sin agrupación por día).

    Para cada atleta:
    1. Pide las ``latest_limit`` actividades más recientes a Strava.
    2. Lista los blobs existentes en ``pipeline/research-wiki-input/`` — cada
       blob se nombra con el ID de la actividad — para deduplicar.
    3. Por cada actividad nueva: escribe un blob marcador con ese ID y crea
       el documento en Firestore ``activities_runs/{activity_id}`` con los
       datos básicos y ``status='queued'``.
    """
    state_store = AthleteStateStore()
    artifact_store = ArtifactStore()

    if athlete_ids_csv.strip():
        targets = _resolve_targets(state_store, athlete_ids_csv)
    else:
        targets = connector.list_syncable_athletes()

    now_epoch = int(datetime.now(timezone.utc).timestamp())

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

        athlete_report: dict[str, Any] = {
            "athlete_id": athlete_id,
            "fetched_activities": 0,
            "new_activities": 0,
            "skipped_existing": 0,
            "queued_activity_ids": [],
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
            # Prefer the simple "last N" fetch when the connector supports it.
            fetch_latest = getattr(connector, "fetch_latest_activities", None)
            if callable(fetch_latest):
                activities = fetch_latest(athlete_id, limit=latest_limit)
            else:
                activities = connector.fetch_activities(
                    athlete_id,
                    after_epoch=0,
                    max_pages=1,
                    per_page=latest_limit,
                )

            athlete_report["fetched_activities"] = len(activities)
            existing_ids = _existing_activity_ids_in_bucket(artifact_store)

            for activity in activities:
                activity_id = _safe_int(activity.get("id"), 0)
                if activity_id <= 0:
                    continue

                activity_id_str = str(activity_id)
                if activity_id_str in existing_ids:
                    athlete_report["skipped_existing"] += 1
                    continue

                # Fetch full detail and merge over summary (detail wins on conflict).
                detail = connector.fetch_activity_detail(athlete_id, activity_id)
                if isinstance(detail, dict):
                    activity = {**activity, **detail}

                # 1. Write the activity blob: filename = activity id, content =
                #    full Strava activity payload serialized as JSON.
                blob_path = f"{_RESEARCH_INPUT_PREFIX}/{activity_id_str}"
                activity_json = json.dumps(activity, ensure_ascii=False, default=str)
                artifact_store.write_text(blob_path, activity_json)
                existing_ids.add(activity_id_str)

                # 2. Upsert the Firestore tracking document as queued.
                firestore_payload = _build_activity_firestore_payload(activity, athlete_id)
                firestore_payload.update(
                    {
                        "status": "queued",
                        "queued_at": utc_now_iso(),
                        "blob_path": blob_path,
                        "source": connector.connector_name,
                    }
                )
                state_store.upsert_activity_run(activity_id, firestore_payload)

                athlete_report["new_activities"] += 1
                athlete_report["queued_activity_ids"].append(activity_id)

            state_store.update_sync_state(
                athlete_id,
                last_sync_epoch=now_epoch,
                status="success",
                details={
                    "fetched_activities": athlete_report["fetched_activities"],
                    "new_activities": athlete_report["new_activities"],
                    "skipped_existing": athlete_report["skipped_existing"],
                },
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
    latest_limit: int = _LATEST_ACTIVITIES_LIMIT,
) -> dict[str, Any]:
    """Wrapper de compatibilidad que usa el conector de Strava."""
    state_store = AthleteStateStore()
    connector = StravaConnector(state_store)
    return run_ingestion(
        connector,
        athlete_ids_csv=athlete_ids_csv,
        latest_limit=latest_limit,
    )


def _build_research_record_from_activity_payload(
    activity_payload: dict[str, Any],
    athlete_id: int,
) -> dict[str, Any]:
    """Construye el registro que consume el agente de deep research a partir
    del documento almacenado en ``activities_runs``.
    """
    name = str(activity_payload.get("name") or "")
    sport = str(activity_payload.get("sport_type") or activity_payload.get("type") or "")
    summary = f"{name} ({sport})".strip()
    text_for_embedding = f"{name} {sport} {summary}".strip()

    activity_id = _safe_int(activity_payload.get("activity_id") or activity_payload.get("id"), 0)

    record: dict[str, Any] = {
        "_id": f"{athlete_id}_{activity_id}",
        "id": activity_id,
        "athlete_id": athlete_id,
        "text": text_for_embedding,
        "_text": text_for_embedding,
        "summary": summary,
    }

    flat_fields = [
        "name", "distance", "moving_time", "elapsed_time", "total_elevation_gain",
        "type", "sport_type", "workout_type", "device_name", "start_date", "start_date_local",
        "timezone", "average_speed", "max_speed", "average_temp", "average_watts",
        "kilojoules", "has_heartrate", "average_heartrate", "max_heartrate",
        "elev_high", "elev_low", "pr_count", "achievement_count", "kudos_count",
        "athlete_count", "gear_id", "trainer", "commute", "manual",
    ]
    for field in flat_fields:
        value = activity_payload.get(field)
        if value is not None:
            record[field] = value
    return record


# ─── Stage 4: Deep Research Wiki ────────────────────────────────────────────


def _average(values: list[float]) -> float | None:
    cleaned = [value for value in values if value > 0]
    if not cleaned:
        return None
    return sum(cleaned) / float(len(cleaned))


def _aggregate_activity_metrics(record: dict[str, Any]) -> dict[str, Any]:
    """Resumen básico de una sola actividad para el prompt del agente."""
    distance_km = _safe_float(record.get("distance"), 0.0) / 1000.0
    moving_hours = _safe_float(record.get("moving_time"), 0.0) / 3600.0
    sport = str(record.get("sport_type") or record.get("type") or "unknown").strip().lower() or "unknown"

    return {
        "target_activities": 1,
        "target_distance_km": round(distance_km, 2),
        "target_moving_hours": round(moving_hours, 2),
        "target_avg_heartrate": _safe_float(record.get("average_heartrate"), 0.0) or None,
        "target_avg_watts": _safe_float(record.get("average_watts"), 0.0) or None,
        "sport_distribution": {sport: 1},
    }



def _merge_research_step_into_daily_run(
    *,
    state_store: AthleteStateStore,
    daily_run_id: str,
    step_payload: dict[str, Any],
    run_status: str | None = None,
) -> None:
    normalized_daily_run_id = (daily_run_id or "").strip()
    if not normalized_daily_run_id:
        return

    daily_run = state_store.get_pipeline_run(normalized_daily_run_id) or {}
    if not isinstance(daily_run, dict):
        daily_run = {}

    steps = daily_run.get("steps")
    if not isinstance(steps, dict):
        steps = {}

    existing_step = steps.get("research_wiki")
    merged_step: dict[str, Any] = {}
    if isinstance(existing_step, dict):
        merged_step.update(existing_step)
    merged_step.update(step_payload)
    merged_step.setdefault("stage", "research_wiki")
    steps["research_wiki"] = merged_step

    daily_run["run_id"] = str(daily_run.get("run_id") or normalized_daily_run_id)
    daily_run["steps"] = steps
    if run_status:
        daily_run["status"] = run_status
    daily_run["updated_at"] = utc_now_iso()

    state_store.record_pipeline_run(normalized_daily_run_id, daily_run)


# ─── Wiki page helpers ────────────────────────────────────────────────────


def _read_wiki_page(artifact_store: ArtifactStore, athlete_id: int, slug: str) -> str | None:
    return artifact_store.read_text(f"wiki/{athlete_id}/{slug}.md")


def _write_wiki_page(artifact_store: ArtifactStore, athlete_id: int, slug: str, content: str) -> str:
    return artifact_store.write_text(f"wiki/{athlete_id}/{slug}.md", content)


def _extract_and_index_graph(
    artifact_store: ArtifactStore,
    athlete_id: int,
    slug: str,
    content: str,
) -> None:
    import sys
    try:
        graph = _extract_graph_from_content(athlete_id=athlete_id, slug=slug, content=content)
    except Exception as exc:  # noqa: BLE001
        print(f"[KG] extraction failed for {athlete_id}/{slug}: {exc}", file=sys.stderr)
        return
    graph_path = f"graph/{athlete_id}/{slug}.json"
    try:
        artifact_store.write_json(graph_path, graph)
    except Exception as exc:  # noqa: BLE001
        print(f"[KG] GCS write failed for {athlete_id}/{slug}: {exc}", file=sys.stderr)
        return
    try:
        _vector_index_graph_page(athlete_id, slug, graph)
    except Exception as exc:  # noqa: BLE001
        print(f"[KG] Pinecone index failed for {athlete_id}/{slug}: {exc}", file=sys.stderr)


def _read_all_wiki_pages(artifact_store: ArtifactStore, athlete_id: int) -> dict[str, str]:
    pages: dict[str, str] = {}
    prefix = f"wiki/{athlete_id}/"
    for path in artifact_store.list_paths(prefix=prefix, suffix=".md"):
        content = artifact_store.read_text(path)
        if content:
            slug = str(path).split("/")[-1].replace(".md", "")
            pages[slug] = content
    return pages


def _append_log_entry(artifact_store: ArtifactStore, athlete_id: int, entry: str) -> None:
    log_path = f"wiki/{athlete_id}/_log.md"
    existing = artifact_store.read_text(log_path) or ""
    artifact_store.write_text(log_path, existing + entry + "\n")


def _extract_athlete_name(profile: dict[str, Any], athlete_id: int) -> str:
    first = str(profile.get("firstname") or "").strip()
    last = str(profile.get("lastname") or "").strip()
    name = f"{first} {last}".strip()
    return name or f"Atleta {athlete_id}"


def _build_activity_summary_for_prompt(record: dict[str, Any], metrics: dict[str, Any]) -> str:
    sport = str(record.get("sport_type") or record.get("type") or "Unknown").strip()
    name = str(record.get("name") or "").strip()
    dist_km = round(_safe_float(metrics.get("target_distance_km"), 0.0), 1)
    hours = round(_safe_float(metrics.get("target_moving_hours"), 0.0), 2)
    hr = metrics.get("target_avg_heartrate")
    watts = metrics.get("target_avg_watts")

    parts = [f"{sport}: {name}" if name else sport]
    if dist_km > 0:
        parts.append(f"{dist_km}km")
    if hours > 0:
        mins = int(hours * 60)
        parts.append(f"{mins}min")
    if hr:
        parts.append(f"HR {int(hr)}")
    if watts:
        parts.append(f"{int(watts)}W")
    return " | ".join(parts)


def research_wiki_pipeline(
    athlete_ids_csv: str = "",
    daily_run_id: str = "",
    max_activities: int = 100,
) -> dict[str, Any]:
    """Procesa las actividades encoladas en Firestore (``status='queued'``).

    Para cada actividad:
    1. Lee los datos básicos de ``activities_runs/{activity_id}``.
    2. Ejecuta el agente de deep research contra ese registro.
    3. Escribe/actualiza ``wiki/{athlete_id}/research.md``.
    4. Marca la actividad como ``success`` en Firestore.
    """
    artifact_store = ArtifactStore()
    state_store = AthleteStateStore()

    run_id = uuid.uuid4().hex
    parent_daily_run_id = (daily_run_id or "").strip() or None
    started_at = utc_now_iso()

    # Resolve target athletes (CSV override or all athletes with tokens).
    targets = _resolve_targets(state_store, athlete_ids_csv) if athlete_ids_csv.strip() else []
    target_athlete_ids: set[int] = {
        _safe_int(target.get("athlete_id"), 0)
        for target in targets
        if _safe_int(target.get("athlete_id"), 0) > 0
    }
    athlete_profiles: dict[int, dict[str, Any]] = {}
    for target in targets:
        athlete_id = _safe_int(target.get("athlete_id"), 0)
        profile = target.get("profile") if isinstance(target.get("profile"), dict) else {}
        athlete_profiles[athlete_id] = profile or {}

    report: dict[str, Any] = {
        "stage": "research_wiki",
        "run_id": run_id,
        "daily_run_id": parent_daily_run_id,
        "started_at": started_at,
        "activities": [],
        "errors": [],
    }

    running_step_payload = {
        "stage": "research_wiki",
        "run_id": run_id,
        "status": "running",
        "started_at": started_at,
    }

    if parent_daily_run_id:
        _merge_research_step_into_daily_run(
            state_store=state_store,
            daily_run_id=parent_daily_run_id,
            step_payload=running_step_payload,
            run_status="running",
        )
    else:
        state_store.record_pipeline_run(
            run_id,
            {
                "run_id": run_id,
                "stage": "research_wiki",
                "status": "running",
                "daily_run_id": parent_daily_run_id,
                "started_at": started_at,
            },
        )

    # Query queued activities in Firestore.
    queued_activities = state_store.list_activity_runs_by_status(
        "queued",
        limit=max(1, int(max_activities)),
    )

    # If a CSV of athletes was provided, restrict processing to those athletes.
    if target_athlete_ids:
        queued_activities = [
            activity for activity in queued_activities
            if _safe_int(activity.get("athlete_id"), 0) in target_athlete_ids
        ]

    for activity_payload in queued_activities:
        activity_id = _safe_int(activity_payload.get("activity_id") or activity_payload.get("id"), 0)
        athlete_id = _safe_int(activity_payload.get("athlete_id"), 0)
        if activity_id <= 0 or athlete_id <= 0:
            continue

        # Mark as running to avoid double-processing.
        state_store.update_activity_run_status(
            activity_id,
            "running",
            details={"run_id": run_id},
        )

        record = _build_research_record_from_activity_payload(activity_payload, athlete_id)
        metrics = _aggregate_activity_metrics(record)

        athlete_profile = athlete_profiles.get(athlete_id)
        if not isinstance(athlete_profile, dict):
            athlete_doc = state_store.get_athlete(athlete_id) or {}
            profile = athlete_doc.get("profile") if isinstance(athlete_doc.get("profile"), dict) else {}
            athlete_profile = profile or {}

        target_day = _activity_day(record)
        athlete_name = _extract_athlete_name(athlete_profile, athlete_id)
        activity_data: dict[str, Any] = {
            "record": record,
            "metrics": metrics,
            "athlete_profile": athlete_profile,
            "target_date": target_day,
        }

        index_content = _read_wiki_page(artifact_store, athlete_id, "_index")
        is_bootstrap = index_content is None
        affected_slugs: list[str] = []
        page_errors: list[str] = []

        try:
            if is_bootstrap:
                # First activity: generate all pages at once.
                pages = bootstrap_wiki(activity_data, athlete_name)
                for slug, content in pages.items():
                    _write_wiki_page(artifact_store, athlete_id, slug, content)
                affected_slugs = list(pages.keys())
                # Index every page in the vector store (skip _index / _log).
                _vector_index_pages(
                    athlete_id,
                    {s: c for s, c in pages.items() if not s.startswith("_")},
                )
                for s, c in pages.items():
                    if not s.startswith("_"):
                        _extract_and_index_graph(artifact_store, athlete_id, s, c)
            else:
                # Incremental: triage then update affected pages.
                affected_slugs = triage_activity(activity_data, index_content)

                for slug in affected_slugs:
                    try:
                        current = _read_wiki_page(artifact_store, athlete_id, slug)
                        updated, graph = update_page_with_graph(
                            slug, current, activity_data, athlete_name, athlete_id
                        )
                        _write_wiki_page(artifact_store, athlete_id, slug, updated)
                        if not slug.startswith("_"):
                            _vector_index_page(athlete_id, slug, updated)
                            graph_path = f"graph/{athlete_id}/{slug}.json"
                            try:
                                artifact_store.write_json(graph_path, graph)
                                _vector_index_graph_page(athlete_id, slug, graph)
                            except Exception as kg_exc:  # noqa: BLE001
                                import sys
                                print(f"[KG] index failed for {athlete_id}/{slug}: {kg_exc}", file=sys.stderr)
                    except Exception as page_exc:  # noqa: BLE001
                        page_errors.append(f"{slug}: {page_exc}")

                # Regenerate _index.md
                page_summaries: dict[str, str] = {}
                for slug in PAGE_SLUGS:
                    page_content = _read_wiki_page(artifact_store, athlete_id, slug)
                    if page_content:
                        page_summaries[slug] = page_content[:500]
                try:
                    new_index = generate_index(page_summaries, athlete_name)
                    _write_wiki_page(artifact_store, athlete_id, "_index", new_index)
                except Exception:  # noqa: BLE001
                    page_errors.append("_index: failed to regenerate")

        except Exception as exc:  # noqa: BLE001
            error_message = f"wiki_update_failed: {exc}"
            report["errors"].append(
                {"activity_id": activity_id, "athlete_id": athlete_id, "error": error_message}
            )
            state_store.update_activity_run_status(
                activity_id,
                "failed",
                details={"run_id": run_id, "error": error_message},
            )
            continue

        # Append log entry
        activity_summary = _build_activity_summary_for_prompt(record, metrics)
        log_entry = format_log_entry(target_day, "analyze", activity_summary, affected_slugs)
        _append_log_entry(artifact_store, athlete_id, log_entry)

        # Determine status
        if page_errors:
            status_label = "partial_success"
            state_store.update_activity_run_status(
                activity_id,
                "partial_success",
                details={
                    "run_id": run_id,
                    "affected_pages": affected_slugs,
                    "page_errors": page_errors,
                    "processed_at": utc_now_iso(),
                },
            )
            for pe in page_errors:
                report["errors"].append(
                    {"activity_id": activity_id, "athlete_id": athlete_id, "error": pe}
                )
        else:
            status_label = "compiled"
            state_store.update_activity_run_status(
                activity_id,
                "success",
                details={
                    "run_id": run_id,
                    "affected_pages": affected_slugs,
                    "processed_at": utc_now_iso(),
                },
            )

        wiki_dir = f"wiki/{athlete_id}/"
        report["activities"].append(
            {
                "activity_id": activity_id,
                "athlete_id": athlete_id,
                "target_date": target_day,
                "status": status_label,
                "incremental_update": not is_bootstrap,
                "affected_pages": affected_slugs,
                "report_path": wiki_dir,
                "storage_mode": artifact_store.mode,
            }
        )

    report["ok"] = not report["errors"]
    report["finished_at"] = utc_now_iso()
    report["queued_processed"] = len(report["activities"])
    if not report["activities"] and not report["errors"]:
        report["status"] = "skipped"
    elif report["ok"]:
        report["status"] = "success"
    else:
        report["status"] = "partial_failure"

    if parent_daily_run_id:
        _merge_research_step_into_daily_run(
            state_store=state_store,
            daily_run_id=parent_daily_run_id,
            step_payload={
                "stage": "research_wiki",
                "run_id": run_id,
                "status": report["status"],
                "activities": report["activities"],
                "errors": report["errors"],
                "ok": report["ok"],
                "queued_processed": report["queued_processed"],
                "started_at": started_at,
                "finished_at": report["finished_at"],
            },
            run_status=report["status"],
        )
    else:
        state_store.record_pipeline_run(run_id, report)

    return report



# ─── Daily Pipeline ─────────────────────────────────────────────────────────

def run_daily_pipeline(
    athlete_ids_csv: str = "",
    target_date: str = "",
    latest_limit: int = _LATEST_ACTIVITIES_LIMIT,
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
    ingestion_report = run_ingestion(
        connector,
        athlete_ids_csv=athlete_ids_csv,
        latest_limit=latest_limit,
    )

    pipeline_report = {
        "run_id": run_id,
        "target_date": day,
        "status": "success",
        "finished_at": utc_now_iso(),
        "steps": {
            "ingestion": ingestion_report,
        },
    }

    state_store.record_pipeline_run(run_id, pipeline_report)
    return pipeline_report


# ─── Pipeline wrappers (JSON string output for agent tools) ─────────────────

def run_ingestion_pipeline(
    athlete_ids_csv: str = "",
    latest_limit: int = _LATEST_ACTIVITIES_LIMIT,
) -> str:
    state_store = AthleteStateStore()
    connector = StravaConnector(state_store)
    return json.dumps(
        run_ingestion(
            connector,
            athlete_ids_csv=athlete_ids_csv,
            latest_limit=latest_limit,
        ),
        ensure_ascii=False,
    )


def run_research_wiki_pipeline(
    athlete_ids_csv: str = "",
    daily_run_id: str = "",
    max_activities: int = 100,
) -> str:
    return json.dumps(
        research_wiki_pipeline(
            athlete_ids_csv=athlete_ids_csv,
            daily_run_id=daily_run_id,
            max_activities=max_activities,
        ),
        ensure_ascii=False,
    )


_QUERY_TOKEN_RE = re.compile(r"[a-zA-Z0-9áéíóúñü]+", flags=re.IGNORECASE)


def _tokenize_for_query(text: str) -> set[str]:
    if not isinstance(text, str):
        return set()
    return {
        token.lower()
        for token in _QUERY_TOKEN_RE.findall(text)
        if len(token) >= 3
    }


def _split_markdown_for_query(markdown_text: str) -> list[str]:
    lines = markdown_text.splitlines()
    sections: list[str] = []
    current: list[str] = []

    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("## ") or stripped.startswith("### "):
            if current:
                section_text = "\n".join(current).strip()
                if section_text:
                    sections.append(section_text)
                current = []
        current.append(line)

    if current:
        section_text = "\n".join(current).strip()
        if section_text:
            sections.append(section_text)

    if sections:
        return sections

    paragraphs = [chunk.strip() for chunk in markdown_text.split("\n\n") if chunk.strip()]
    if paragraphs:
        return paragraphs

    fallback = markdown_text.strip()
    return [fallback] if fallback else []


def _score_query_chunk(chunk_text: str, question_terms: set[str]) -> float:
    if not chunk_text:
        return 0.0

    if not question_terms:
        return 0.01

    lowered = chunk_text.lower()
    matched_terms = sum(1 for term in question_terms if term in lowered)
    if matched_terms <= 0:
        return 0.0

    coverage = matched_terms / max(1, len(question_terms))
    return round(coverage, 6)


def _build_wiki_hits(
    *,
    athlete_id: int,
    report_markdown: str,
    question: str,
    top_k: int,
) -> list[dict[str, Any]]:
    source_path = f"wiki/{athlete_id}/research.md"
    question_terms = _tokenize_for_query(question)
    sections = _split_markdown_for_query(report_markdown)

    scored_sections: list[tuple[float, int, str]] = []
    for index, section in enumerate(sections):
        clean_section = section.strip()
        if not clean_section:
            continue
        score = _score_query_chunk(clean_section, question_terms)
        scored_sections.append((score, index, clean_section))

    if not scored_sections:
        return []

    ranked_sections = sorted(
        scored_sections,
        key=lambda item: (item[0], -item[1]),
        reverse=True,
    )

    selected = [item for item in ranked_sections if item[0] > 0][:top_k]
    if not selected:
        selected = sorted(scored_sections, key=lambda item: item[1])[:top_k]

    return [
        {
            "score": score,
            "type": "wiki_research",
            "source_path": source_path,
            "text": text[:1500],
        }
        for score, _index, text in selected
    ]


def run_query_layer(
    question: str,
    athlete_id: int,
    top_k: int = 5,
    target_date: str = "",
) -> dict[str, Any]:
    normalized_top_k = max(1, int(top_k))
    normalized_target_date = target_date.strip() if isinstance(target_date, str) else ""
    source_path = f"wiki/{athlete_id}/"

    artifact_store = ArtifactStore()
    wiki_pages = _read_all_wiki_pages(artifact_store, athlete_id)
    if not wiki_pages:
        return {
            "mode": "wiki_missing",
            "athlete_id": athlete_id,
            "hits": [],
            "context": "",
            "target_date": _normalize_date(normalized_target_date),
            "source_path": source_path,
            "error": "wiki_not_found",
            "graph_hits": [],
        }

    # Concatenate all wiki pages for search
    report_markdown = "\n\n---\n\n".join(
        f"## Página: {slug}\n\n{content}"
        for slug, content in sorted(wiki_pages.items())
    )

    hits = _build_wiki_hits(
        athlete_id=athlete_id,
        report_markdown=report_markdown,
        question=question,
        top_k=normalized_top_k,
    )
    context_text = "\n\n".join(
        f"[{hit.get('type')}] {hit.get('text', '')}"
        for hit in hits
        if str(hit.get("text", "")).strip()
    )

    graph_hits: list[dict[str, Any]] = []
    graph_slug_hits = _retrieve_graph_context(athlete_id, question, top_k=3)
    if isinstance(graph_slug_hits, list):
        for hit in graph_slug_hits:
            graph_path = f"graph/{athlete_id}/{hit['slug']}.json"
            graph_data = artifact_store.read_json(graph_path)
            if isinstance(graph_data, dict):
                graph_hits.append({
                    "slug": hit["slug"],
                    "triples": graph_data.get("triples") or [],
                    "paths": graph_data.get("paths") or [],
                    "embedding_text": graph_data.get("embedding_text") or "",
                    "type": "graph",
                })

    return {
        "mode": "wiki_research",
        "athlete_id": athlete_id,
        "hits": hits,
        "context": context_text,
        "target_date": _normalize_date(normalized_target_date),
        "source_path": source_path,
        "graph_hits": graph_hits,
    }


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
    latest_limit: int = _LATEST_ACTIVITIES_LIMIT,
) -> str:
    return json.dumps(
        run_daily_pipeline(
            athlete_ids_csv=athlete_ids_csv,
            target_date=target_date,
            latest_limit=latest_limit,
        ),
        ensure_ascii=False,
    )
