from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import requests

from .storage_backend import ArtifactStore, AthleteStateStore, utc_now_iso

try:
    from pinecone import Pinecone as _Pinecone
except Exception:  # noqa: BLE001
    _Pinecone = None

try:
    from google import genai as _genai
    from google.genai import types as _genai_types
except Exception:  # noqa: BLE001
    _genai = None
    _genai_types = None

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


# ─── Pinecone helpers ────────────────────────────────────────────────────────

_PINECONE_SUMMARY_INSTRUCTION = (
    "You are a sports analytics assistant. "
    "Given a Strava activity JSON, write a concise summary in at most 50 words. "
    "Focus on: sport type, distance, duration, elevation, intensity, and any notable metrics. "
    "Write in the same language as the activity name. Be factual and specific."
)

_WIKI_ARTICLES = [
    "profile.md",
    "training_summary.md",
    "performance_trends.md",
    "fatigue_recovery.md",
    "insights.md",
]

_MAX_WIKI_ROUNDS = 4

_WIKI_COMPILER_PROMPT = (
    "You are a sports knowledge base compiler. Given raw activity data from Pinecone and "
    "an optional existing wiki, generate or update a structured athlete wiki.\n\n"
    "Output format: emit each article separated by a line containing only '---ARTICLE: filename.md---'.\n"
    "The filenames MUST be exactly: profile.md, training_summary.md, performance_trends.md, "
    "fatigue_recovery.md, insights.md.\n\n"
    "Rules for each article:\n"
    "- profile.md: Athlete profile — sports practiced, level, equipment, location.\n"
    "- training_summary.md: Recent training overview — volume, frequency, types of sessions.\n"
    "- performance_trends.md: Trends — distance, speed, elevation over time. Improvements and declines.\n"
    "- fatigue_recovery.md: Fatigue and recovery signals — training load, rest days, intensity patterns.\n"
    "- insights.md: Key insights, recommendations, contradictions between old and new data.\n\n"
    "Each article must:\n"
    "- Be written in Markdown.\n"
    "- Include backlinks to related articles (e.g., '[see trends](performance_trends.md)').\n"
    "- Include a '## Sources' section listing Pinecone activity IDs used.\n"
    "- If existing wiki content is provided, update it incrementally — preserve what is still valid, "
    "revise what has changed, flag contradictions.\n"
    "- Write in Spanish.\n"
    "- Be concise but thorough."
)


def _get_pinecone_index() -> Any:
    if _Pinecone is None:
        raise RuntimeError("pinecone package is not installed")
    api_key = os.environ.get("PINECONE_API_KEY", "")
    if not api_key:
        raise RuntimeError("PINECONE_API_KEY environment variable is not set")
    pc = _Pinecone(api_key=api_key)
    index_name = os.environ.get("PINECONE_INDEX_NAME", "strava-agent")
    return pc.Index(index_name)


def _get_pinecone_namespace(athlete_id: int) -> str:
    return str(_safe_int(athlete_id, 0))


def _get_genai_client() -> Any:
    if _genai is None:
        raise RuntimeError("google-genai package is not installed")
    return _genai.Client()


def _generate_activity_summary(client: Any, activity_data: dict[str, Any]) -> str:
    sanitized = {
        k: v for k, v in activity_data.items()
        if k not in {"map", "start_latlng", "end_latlng", "external_id", "upload_id", "upload_id_str"}
    }
    try:
        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            config=_genai_types.GenerateContentConfig(
                system_instruction=_PINECONE_SUMMARY_INSTRUCTION,
            ),
            contents=json.dumps(sanitized, ensure_ascii=False),
        )
        return (response.text or "").strip()[:300]
    except Exception:  # noqa: BLE001
        name = str(activity_data.get("name") or "Activity")
        sport = str(activity_data.get("sport_type") or activity_data.get("type") or "")
        dist = _safe_float(activity_data.get("distance"), 0.0) / 1000.0
        return f"{name} ({sport}) - {dist:.1f} km"


def _build_pinecone_metadata(activity: dict[str, Any], summary: str, athlete_id: int) -> dict[str, Any]:
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


# ─── Stage 2: Pinecone Indexing ──────────────────────────────────────────────

def run_pinecone_indexing(
    athlete_ids_csv: str = "",
    target_date: str = "",
) -> dict[str, Any]:
    artifact_store = ArtifactStore()
    state_store = AthleteStateStore()

    day = _normalize_date(target_date)
    targets = _resolve_targets(state_store, athlete_ids_csv)

    report: dict[str, Any] = {
        "stage": "pinecone_indexing",
        "target_date": day,
        "athletes": [],
        "errors": [],
    }

    try:
        pc_index = _get_pinecone_index()
        genai_client = _get_genai_client()
    except RuntimeError as exc:
        report["errors"].append({"error": str(exc)})
        return report

    for target in targets:
        athlete_id = _safe_int(target.get("athlete_id"), 0)
        if athlete_id <= 0:
            continue

        namespace = _get_pinecone_namespace(athlete_id)

        paths = _latest_activity_paths_for_day(artifact_store, athlete_id, day)
        if not paths:
            report["athletes"].append(
                {"athlete_id": athlete_id, "status": "skipped", "reason": "no_activities_for_date"}
            )
            continue

        records: list[dict[str, Any]] = []
        for path in paths:
            activity = artifact_store.read_json(path)
            if not isinstance(activity, dict):
                continue

            activity_id = _safe_int(activity.get("id"), 0)
            if activity_id <= 0:
                continue

            summary = _generate_activity_summary(genai_client, activity)
            metadata = _build_pinecone_metadata(activity, summary, athlete_id)

            name = str(activity.get("name") or "")
            sport = str(activity.get("sport_type") or activity.get("type") or "")
            text_for_embedding = f"{name} {sport} {summary}"

            records.append({
                "_id": f"{athlete_id}_{activity_id}",
                # Keep both keys to support existing indexes configured with either field map.
                "text": text_for_embedding,
                "_text": text_for_embedding,
                **metadata,
            })

        if records:
            try:
                pc_index.upsert_records(namespace=namespace, records=records)
            except Exception as exc:  # noqa: BLE001
                report["errors"].append({"athlete_id": athlete_id, "error": str(exc)})
                continue

        state_store.set_last_indexed_date(athlete_id, day)

        report["athletes"].append({
            "athlete_id": athlete_id,
            "status": "indexed",
            "records_upserted": len(records),
        })

    report["ok"] = not report["errors"]
    report["finished_at"] = utc_now_iso()
    return report


# ─── Stage 3: RAG Wiki Pipeline (Karpathy approach) ─────────────────────────

def _load_existing_wiki(artifact_store: ArtifactStore, athlete_id: int) -> dict[str, str]:
    wiki: dict[str, str] = {}
    for filename in _WIKI_ARTICLES:
        text = artifact_store.read_text(f"wiki/athletes/{athlete_id}/{filename}")
        if isinstance(text, str) and text.strip():
            wiki[filename] = text
    return wiki


def _parse_wiki_articles(llm_output: str) -> dict[str, str]:
    articles: dict[str, str] = {}
    valid_filenames = set(_WIKI_ARTICLES)

    parts = re.split(r"---ARTICLE:\s*(\S+)\s*---", llm_output)
    # parts[0] is before first marker, then alternating: filename, content, filename, content...
    for i in range(1, len(parts) - 1, 2):
        filename = parts[i].strip()
        content = parts[i + 1].strip()
        if filename in valid_filenames and content:
            articles[filename] = content

    return articles


def _pinecone_search(pc_index: Any, namespace: str, query: str, athlete_id: int, top_k: int = 10) -> list[dict[str, Any]]:
    try:
        results = pc_index.search_records(
            namespace=namespace,
            query={"inputs": {"text": query}, "top_k": top_k},
            filter={"athlete_id": {"$eq": athlete_id}},
        )
        hits = []
        result_data = results if isinstance(results, dict) else {}
        for hit in result_data.get("result", {}).get("hits", []):
            fields = hit.get("fields", {})
            hits.append({
                "id": hit.get("_id", ""),
                "score": hit.get("_score", 0.0),
                "summary": fields.get("summary", ""),
                "name": fields.get("name", ""),
                "sport_type": fields.get("sport_type", ""),
                "distance": fields.get("distance", 0),
                "moving_time": fields.get("moving_time", 0),
                "start_date_local": fields.get("start_date_local", ""),
            })
        return hits
    except Exception:  # noqa: BLE001
        return []


def _parse_questions(llm_text: str) -> list[str]:
    questions = []
    for line in llm_text.strip().splitlines():
        line = line.strip().lstrip("0123456789.-) ")
        if line and "?" in line:
            questions.append(line)
    return questions[:3] if questions else []


def rag_wiki_pipeline(
    athlete_ids_csv: str = "",
    target_date: str = "",
) -> dict[str, Any]:
    artifact_store = ArtifactStore()
    state_store = AthleteStateStore()
    day = _normalize_date(target_date)
    targets = _resolve_targets(state_store, athlete_ids_csv)

    report: dict[str, Any] = {
        "stage": "rag_wiki",
        "target_date": day,
        "athletes": [],
        "errors": [],
    }

    try:
        pc_index = _get_pinecone_index()
        genai_client = _get_genai_client()
    except RuntimeError as exc:
        report["errors"].append({"error": str(exc)})
        return report

    for target in targets:
        athlete_id = _safe_int(target.get("athlete_id"), 0)
        if athlete_id <= 0:
            continue

        namespace = _get_pinecone_namespace(athlete_id)

        existing_wiki = _load_existing_wiki(artifact_store, athlete_id)

        # Iterative Pinecone querying
        accumulated_context: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        questions = [
            f"Resumen general de actividades recientes del atleta {athlete_id}",
            f"Tipos de deporte y distancias del atleta {athlete_id}",
        ]

        rounds_executed = 0
        for round_num in range(_MAX_WIKI_ROUNDS):
            rounds_executed = round_num + 1
            for q in questions:
                hits = _pinecone_search(pc_index, namespace, q, athlete_id, top_k=10)
                for hit in hits:
                    hit_id = str(hit.get("id", ""))
                    if hit_id and hit_id not in seen_ids:
                        seen_ids.add(hit_id)
                        accumulated_context.append(hit)

            if round_num >= _MAX_WIKI_ROUNDS - 1:
                break

            # Ask LLM for follow-up questions or DONE
            context_summary = json.dumps(
                [{"name": h.get("name"), "sport": h.get("sport_type"), "summary": h.get("summary")} for h in accumulated_context[-20:]],
                ensure_ascii=False,
            )
            try:
                next_step = genai_client.models.generate_content(
                    model="gemini-3-flash-preview",
                    config=_genai_types.GenerateContentConfig(
                        system_instruction=(
                            "Eres un analista deportivo. Dado el contexto de actividades de un atleta, "
                            "formula 2-3 preguntas de profundizacion para conocer mejor su rendimiento, "
                            "tendencias y estado de fatiga. Si crees que tienes suficiente informacion "
                            "para escribir un perfil completo, responde solo con la palabra DONE."
                        ),
                    ),
                    contents=context_summary,
                )
                response_text = (next_step.text or "").strip()
                if "DONE" in response_text.upper():
                    break
                questions = _parse_questions(response_text)
                if not questions:
                    break
            except Exception:  # noqa: BLE001
                break

        if not accumulated_context:
            report["athletes"].append({
                "athlete_id": athlete_id,
                "status": "skipped",
                "reason": "no_context_from_pinecone",
            })
            continue

        # Compile wiki
        compile_input = json.dumps(
            {
                "athlete_id": athlete_id,
                "context": accumulated_context,
                "existing_wiki": existing_wiki,
                "target_date": day,
            },
            ensure_ascii=False,
        )

        try:
            wiki_response = genai_client.models.generate_content(
                model="gemini-3-flash-preview",
                config=_genai_types.GenerateContentConfig(
                    system_instruction=_WIKI_COMPILER_PROMPT,
                ),
                contents=compile_input,
            )
            wiki_text = (wiki_response.text or "").strip()
        except Exception as exc:  # noqa: BLE001
            report["errors"].append({"athlete_id": athlete_id, "error": f"wiki_compilation_failed: {exc}"})
            continue

        articles = _parse_wiki_articles(wiki_text)
        if not articles:
            report["errors"].append({"athlete_id": athlete_id, "error": "wiki_parse_failed_no_articles"})
            continue

        for filename, content in articles.items():
            artifact_store.write_text(f"wiki/athletes/{athlete_id}/{filename}", content)

        report["athletes"].append({
            "athlete_id": athlete_id,
            "status": "compiled",
            "articles_written": list(articles.keys()),
            "rounds_executed": rounds_executed,
            "context_documents": len(accumulated_context),
        })

    report["ok"] = not report["errors"]
    report["finished_at"] = utc_now_iso()
    return report


# ─── Query Layer (Pinecone) ─────────────────────────────────────────────────

def run_query_layer(
    question: str,
    athlete_id: int,
    top_k: int = 5,
    target_date: str = "",
) -> dict[str, Any]:
    normalized_top_k = max(1, int(top_k))
    normalized_target_date = target_date.strip() if isinstance(target_date, str) else ""

    try:
        pc_index = _get_pinecone_index()
    except RuntimeError as exc:
        return {
            "mode": "error",
            "athlete_id": athlete_id,
            "hits": [],
            "context": "",
            "target_date": _normalize_date(normalized_target_date),
            "error": str(exc),
        }

    namespace = _get_pinecone_namespace(athlete_id)
    hits = _pinecone_search(pc_index, namespace, question, athlete_id, top_k=normalized_top_k)

    formatted_hits = [
        {
            "score": hit.get("score", 0.0),
            "type": "activity",
            "source_path": f"pinecone/{hit.get('id', '')}",
            "text": str(hit.get("summary", ""))[:1500],
        }
        for hit in hits
    ]

    context_text = "\n\n".join(
        f"[{hit.get('type')}] {hit.get('text', '')}"
        for hit in formatted_hits
        if str(hit.get("text", "")).strip()
    )

    return {
        "mode": "pinecone",
        "athlete_id": athlete_id,
        "hits": formatted_hits,
        "context": context_text,
        "target_date": _normalize_date(normalized_target_date),
    }


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

    ingestion_report = run_strava_ingestion(athlete_ids_csv=athlete_ids_csv, lookback_days=lookback_days)
    indexing_report = run_pinecone_indexing(athlete_ids_csv=athlete_ids_csv, target_date=day)
    wiki_report = rag_wiki_pipeline(athlete_ids_csv=athlete_ids_csv, target_date=day)

    pipeline_report = {
        "run_id": run_id,
        "target_date": day,
        "window_days": max(2, int(window_days)),
        "status": "success",
        "finished_at": utc_now_iso(),
        "steps": {
            "ingestion": ingestion_report,
            "pinecone_indexing": indexing_report,
            "rag_wiki": wiki_report,
        },
    }

    state_store.record_pipeline_run(run_id, pipeline_report)
    return pipeline_report


# ─── Pipeline wrappers (JSON string output for agent tools) ─────────────────

def run_ingestion_pipeline(athlete_ids_csv: str = "", lookback_days: int = 7) -> str:
    return json.dumps(
        run_strava_ingestion(athlete_ids_csv=athlete_ids_csv, lookback_days=lookback_days),
        ensure_ascii=False,
    )


def run_pinecone_indexing_pipeline(athlete_ids_csv: str = "", target_date: str = "") -> str:
    return json.dumps(
        run_pinecone_indexing(athlete_ids_csv=athlete_ids_csv, target_date=target_date),
        ensure_ascii=False,
    )


def run_rag_wiki_pipeline(athlete_ids_csv: str = "", target_date: str = "") -> str:
    return json.dumps(
        rag_wiki_pipeline(athlete_ids_csv=athlete_ids_csv, target_date=target_date),
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
