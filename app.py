import asyncio
import logging
import os
import secrets
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

import requests
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request, stream_with_context
from flask_cors import CORS

_BASE_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=_BASE_DIR / ".env", override=False)

from agent.service.stream_utils import _stream_generator
from agent.tools.pipeline.storage_backend import AthleteStateStore, utc_now_iso
from strava_agent_sdk import StravaAgentClient
from strava_agent_sdk.errors import ExternalServiceError, NotFoundError, SDKError, ValidationError

app = Flask(__name__)

_DEFAULT_ALLOWED_ORIGINS = [
    "https://strava-adk-agent-frontend.vercel.app",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
_configured_origins = [
    origin.strip()
    for origin in (os.environ.get("CORS_ALLOWED_ORIGINS") or "").split(",")
    if origin.strip()
]
allowed_origins = _configured_origins or _DEFAULT_ALLOWED_ORIGINS
CORS(app, origins=allowed_origins, supports_credentials=True)

sdk_client = StravaAgentClient()


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_strava_access_token(payload: dict[str, Any]) -> str | None:
    for key in ("strava_access_token", "access_token"):
        candidate = payload.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()

    auth_header = (request.headers.get("Authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        bearer_token = auth_header[7:].strip()
        if bearer_token:
            return bearer_token

    return None


def _parse_athlete_ids_csv(data: dict[str, Any]) -> str:
    athlete_ids = data.get("athlete_ids")
    if isinstance(athlete_ids, list):
        normalized_ids = []
        for item in athlete_ids:
            athlete_id = _to_int(item, 0)
            if athlete_id > 0:
                normalized_ids.append(str(athlete_id))
        return ",".join(normalized_ids)

    athlete_ids_csv = data.get("athlete_ids_csv")
    if isinstance(athlete_ids_csv, str):
        return athlete_ids_csv.strip()

    athlete_id = _to_optional_int(data.get("athlete_id"))
    if athlete_id is not None and athlete_id > 0:
        return str(athlete_id)

    return ""


def _internal_request_authorized() -> bool:
    configured_token = (os.environ.get("INTERNAL_PIPELINE_TOKEN") or "").strip()
    if not configured_token:
        return True

    incoming_token = (
        request.headers.get("X-Internal-Token")
        or request.headers.get("Authorization", "").replace("Bearer ", "")
    )
    return incoming_token.strip() == configured_token


def _internal_pipeline_base_url() -> str:
    configured_url = (os.environ.get("INTERNAL_PIPELINE_BASE_URL") or "").strip().rstrip("/")
    if configured_url:
        return configured_url
    port = (os.environ.get("PORT") or "8080").strip() or "8080"
    return f"http://127.0.0.1:{port}"


def _dispatch_index_wiki_async(payload: dict[str, Any]) -> dict[str, Any]:
    dispatch_id = secrets.token_hex(8)
    endpoint = f"{_internal_pipeline_base_url()}/internal/pipeline/index-wiki"

    configured_token = (os.environ.get("INTERNAL_PIPELINE_TOKEN") or "").strip()
    headers = {"Content-Type": "application/json"}
    if configured_token:
        headers["X-Internal-Token"] = configured_token

    def _run_dispatch() -> None:
        try:
            resp = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=1800,
            )
            if not resp.ok:
                logger.error(
                    "index-wiki dispatch %s failed: HTTP %s — %s",
                    dispatch_id,
                    resp.status_code,
                    resp.text[:500],
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("index-wiki dispatch %s raised an exception: %s", dispatch_id, exc)

    worker = threading.Thread(
        target=_run_dispatch,
        name=f"index-wiki-dispatch-{dispatch_id}",
        daemon=True,
    )
    worker.start()

    return {
        "status": "queued",
        "dispatch_id": dispatch_id,
        "endpoint": endpoint,
    }


def _dispatch_research_wiki_async(payload: dict[str, Any]) -> dict[str, Any]:
    dispatch_id = secrets.token_hex(8)
    endpoint = f"{_internal_pipeline_base_url()}/internal/pipeline/research-wiki"

    configured_token = (os.environ.get("INTERNAL_PIPELINE_TOKEN") or "").strip()
    headers = {"Content-Type": "application/json"}
    if configured_token:
        headers["X-Internal-Token"] = configured_token

    def _run_dispatch() -> None:
        try:
            resp = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=1800,
            )
            if not resp.ok:
                logger.error(
                    "research-wiki dispatch %s failed: HTTP %s — %s",
                    dispatch_id,
                    resp.status_code,
                    resp.text[:500],
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("research-wiki dispatch %s raised an exception: %s", dispatch_id, exc)

    worker = threading.Thread(
        target=_run_dispatch,
        name=f"research-wiki-dispatch-{dispatch_id}",
        daemon=True,
    )
    worker.start()

    return {
        "status": "queued",
        "dispatch_id": dispatch_id,
        "endpoint": endpoint,
    }



def _parse_chat_request(
    data: dict[str, Any],
) -> tuple[
    str | None,
    str | None,
    bool,
    int,
    str,
    str | None,
    int | None,
    str,
    str,
]:
    question = data.get("message") or data.get("question")
    model = data.get("model")
    stream_param = data.get("stream", False)
    strava_access_token = _extract_strava_access_token(data)
    strava_athlete_id = data.get("strava_athlete_id")
    response_format = data.get("response_format")
    planner_mode = data.get("planner_mode")

    top_k = max(1, min(_to_int(data.get("top_k"), 5), 20))
    target_date = data.get("target_date")

    if isinstance(stream_param, str):
        stream = stream_param.lower() in ("true", "1", "yes")
    else:
        stream = bool(stream_param)

    model_name = model.strip() if isinstance(model, str) and model.strip() else None

    athlete_id: int | None = None
    athlete_id_raw = data.get("athlete_id")
    if athlete_id_raw is not None:
        athlete_id = _to_optional_int(athlete_id_raw)
    elif strava_athlete_id is not None:
        athlete_id = _to_optional_int(strava_athlete_id)

    normalized_access_token = (
        strava_access_token.strip()
        if isinstance(strava_access_token, str) and strava_access_token.strip()
        else None
    )

    if isinstance(response_format, str) and response_format.strip():
        normalized_response_format = response_format.strip().lower()
    else:
        normalized_response_format = "plan_react_v1"

    if normalized_response_format not in {"plan_react_v1", "structured", "plain"}:
        normalized_response_format = "plan_react_v1"

    if isinstance(planner_mode, str) and planner_mode.strip():
        normalized_planner_mode = planner_mode.strip().lower()
    else:
        normalized_planner_mode = "full_only"

    if normalized_planner_mode not in {"off", "full_only", "always"}:
        normalized_planner_mode = "full_only"

    normalized_target_date = target_date.strip() if isinstance(target_date, str) else ""

    return (
        question,
        model_name,
        stream,
        top_k,
        normalized_target_date,
        normalized_access_token,
        athlete_id,
        normalized_response_format,
        normalized_planner_mode,
    )


@app.get("/health")
def health() -> tuple[dict[str, Any], int]:
    return {
        "status": "ok",
        "architecture": "layered-pipeline-v2",
    }, 200


@app.get("/athletes")
def list_athletes() -> tuple[dict[str, Any], int]:
    if not _internal_request_authorized():
        return {"error": "Unauthorized."}, 401

    try:
        return asyncio.run(sdk_client.list_athletes()), 200
    except SDKError as exc:
        return {"error": str(exc)}, 500
    except Exception as exc:  # noqa: BLE001
        return {"error": "Failed to list athletes.", "details": str(exc)}, 500


@app.post("/pipeline/query")
def query_layer_endpoint() -> tuple[dict[str, Any], int]:
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or data.get("message") or "").strip()
    athlete_id = _to_optional_int(data.get("athlete_id") or data.get("strava_athlete_id"))
    top_k = max(1, min(_to_int(data.get("top_k"), 5), 20))
    target_date = data.get("target_date") if isinstance(data.get("target_date"), str) else ""

    if not question:
        return {"error": "Field 'question' or 'message' is required."}, 400
    if athlete_id is None or athlete_id <= 0:
        return {"error": "Field 'athlete_id' or 'strava_athlete_id' is required."}, 400

    try:
        payload = asyncio.run(sdk_client.query_wiki(
            question=question,
            athlete_id=athlete_id,
            top_k=top_k,
            target_date=target_date,
        ))
        return payload, 200
    except ValidationError as exc:
        return {"error": str(exc)}, 400
    except Exception as exc:  # noqa: BLE001
        return {"error": "Query layer execution failed.", "details": str(exc)}, 500


@app.post("/chat")
@app.post("/ask")
def chat_agent() -> Response | tuple[dict[str, Any], int]:
    data = request.get_json(silent=True) or {}
    (
        question,
        model_name_to_use,
        stream,
        top_k,
        query_target_date,
        strava_access_token,
        strava_athlete_id,
        response_format,
        planner_mode,
    ) = _parse_chat_request(data)

    if not isinstance(question, str) or not question.strip():
        return {"error": "Field 'message' or 'question' must be a non-empty string."}, 400

    if strava_athlete_id is None or strava_athlete_id <= 0:
        return {
            "error": "Field 'strava_athlete_id' or 'athlete_id' is required for multi-athlete retrieval."
        }, 400

    if stream:
        run_streaming_with_sdk = lambda current_question, _agent: sdk_client.chat_stream(
            question=current_question,
            athlete_id=int(strava_athlete_id),
            model_name=model_name_to_use,
            top_k=top_k,
            target_date=query_target_date,
            access_token=strava_access_token,
            response_format=response_format,
            planner_mode=planner_mode,
        )
        return Response(
            stream_with_context(
                _stream_generator(question.strip(), None, run_streaming_with_sdk)
            ),
            content_type="text/event-stream",
            headers={
                "X-Accel-Buffering": "no",
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
            },
        )

    try:
        result = asyncio.run(sdk_client.chat(
            question=question.strip(),
            athlete_id=int(strava_athlete_id),
            model_name=model_name_to_use,
            top_k=top_k,
            target_date=query_target_date,
            access_token=strava_access_token,
            response_format=response_format,
            planner_mode=planner_mode,
        ))
        return jsonify(result.to_payload())
    except ValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    except ExternalServiceError as exc:
        return jsonify({"error": str(exc)}), 400
    except SDKError as exc:
        return jsonify({"error": str(exc)}), 500
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": "Chat execution failed.", "details": str(exc)}), 500


@app.get("/auth/strava/start")
def start_strava_auth() -> tuple[dict[str, Any], int]:
    redirect_uri = (request.args.get("redirect_uri") or "").strip()
    requested_scope = (request.args.get("scope") or "").strip()
    try:
        payload = asyncio.run(sdk_client.start_strava_oauth(
            redirect_uri=redirect_uri,
            scope=requested_scope,
        ))
        return payload, 200
    except ValidationError as exc:
        return {"error": str(exc)}, 400
    except SDKError as exc:
        return {"error": str(exc)}, 500
    except Exception as exc:  # noqa: BLE001
        return {"error": "Strava OAuth start failed.", "details": str(exc)}, 500


@app.post("/auth/strava/exchange")
def exchange_strava_auth_code() -> tuple[dict[str, Any], int]:
    data = request.get_json(silent=True) or {}
    try:
        payload = asyncio.run(sdk_client.exchange_strava_code(
            code=(data.get("code") or "").strip(),
            state=(data.get("state") or "").strip(),
            redirect_uri=(data.get("redirect_uri") or "").strip(),
            scope=(data.get("scope") or "").strip(),
        ))
        return payload, 200
    except ValidationError as exc:
        return {"error": str(exc)}, 400
    except ExternalServiceError as exc:
        return {"error": "Strava token exchange failed.", "details": str(exc)}, 400
    except SDKError as exc:
        return {"error": str(exc)}, 500
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}, 500


@app.post("/auth/strava/refresh")
def refresh_strava_auth_token() -> tuple[dict[str, Any], int]:
    data = request.get_json(silent=True) or {}
    try:
        athlete_id = _to_optional_int(data.get("strava_athlete_id") or data.get("athlete_id"))
        payload = asyncio.run(sdk_client.refresh_strava_token(
            refresh_token=(data.get("refresh_token") or "").strip(),
            athlete_id=athlete_id,
        ))
        return payload, 200
    except ValidationError as exc:
        return {"error": str(exc)}, 400
    except ExternalServiceError as exc:
        return {"error": "Strava token refresh failed.", "details": str(exc)}, 400
    except SDKError as exc:
        return {"error": str(exc)}, 500
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}, 500


@app.post("/internal/pipeline/daily")
@app.post("/pipeline/daily")
def run_daily_pipeline_endpoint() -> tuple[dict[str, Any], int]:
    if not _internal_request_authorized():
        return {"error": "Unauthorized internal pipeline trigger."}, 401

    data = request.get_json(silent=True) or {}
    athlete_ids_csv = _parse_athlete_ids_csv(data)
    target_date = data.get("target_date") if isinstance(data.get("target_date"), str) else ""

    try:
        report = asyncio.run(sdk_client.run_daily_pipeline(
            athlete_ids_csv=athlete_ids_csv,
            target_date=target_date,
        ))

        run_id = str(report.get("run_id") or "").strip()
        queued_at = utc_now_iso()

        dispatch_payload = {
            "athlete_id": _to_optional_int(data.get("athlete_id")),
            "athlete_ids_csv": athlete_ids_csv,
            "daily_run_id": run_id,
        }

        report_steps = report.setdefault("steps", {})
        if not isinstance(report_steps, dict):
            report_steps = {}
            report["steps"] = report_steps
        report_steps.pop("research_wiki_dispatch", None)

        report["status"] = "running"
        report["updated_at"] = queued_at
        report.pop("finished_at", None)

        try:
            dispatch_result = _dispatch_research_wiki_async(dispatch_payload)
            report_steps["research_wiki"] = {
                "stage": "research_wiki",
                "status": "queued",
                "queued_at": queued_at,
                "endpoint": str(dispatch_result.get("endpoint") or ""),
            }
        except Exception as exc:  # noqa: BLE001
            report_steps["research_wiki"] = {
                "stage": "research_wiki",
                "status": "failed",
                "endpoint": f"{_internal_pipeline_base_url()}/internal/pipeline/research-wiki",
                "error": str(exc),
                "finished_at": utc_now_iso(),
            }
            report["status"] = "partial_failure"
            report["finished_at"] = utc_now_iso()
            report["updated_at"] = report["finished_at"]

        report_steps.pop("index_wiki_dispatch", None)
        try:
            index_dispatch_result = _dispatch_index_wiki_async(dispatch_payload)
            report_steps["index_wiki"] = {
                "stage": "index_wiki",
                "status": "queued",
                "queued_at": queued_at,
                "endpoint": str(index_dispatch_result.get("endpoint") or ""),
            }
        except Exception as exc:  # noqa: BLE001
            report_steps["index_wiki"] = {
                "stage": "index_wiki",
                "status": "failed",
                "endpoint": f"{_internal_pipeline_base_url()}/internal/pipeline/index-wiki",
                "error": str(exc),
                "finished_at": utc_now_iso(),
            }
            if report.get("status") != "partial_failure":
                report["status"] = "partial_failure"
                report["finished_at"] = utc_now_iso()
                report["updated_at"] = report["finished_at"]

        if run_id:
            AthleteStateStore().record_pipeline_run(run_id, report)

        return report, 200
    except ValidationError as exc:
        return {"error": str(exc)}, 400
    except Exception as exc:  # noqa: BLE001
        return {"error": "Daily pipeline execution failed.", "details": str(exc)}, 500


@app.post("/internal/pipeline/research-wiki")
@app.post("/pipeline/research-wiki")
def run_research_wiki_endpoint() -> tuple[dict[str, Any], int]:
    if not _internal_request_authorized():
        return {"error": "Unauthorized internal pipeline trigger."}, 401

    data = request.get_json(silent=True) or {}
    athlete_ids_csv = _parse_athlete_ids_csv(data)
    daily_run_id = str(data.get("daily_run_id") or "").strip()
    max_activities = max(1, min(_to_int(data.get("max_activities"), 100), 500))

    try:
        report = asyncio.run(sdk_client.run_research_wiki(
            athlete_ids_csv=athlete_ids_csv,
            daily_run_id=daily_run_id,
            max_activities=max_activities,
        ))
        return report, 200
    except ValidationError as exc:
        return {"error": str(exc)}, 400
    except Exception as exc:  # noqa: BLE001
        return {"error": "Research wiki pipeline execution failed.", "details": str(exc)}, 500


@app.post("/internal/pipeline/index-wiki")
@app.post("/pipeline/index-wiki")
def run_index_wiki_endpoint() -> tuple[dict[str, Any], int]:
    """Re-indexa en Pinecone las páginas existentes de la wiki.

    Acepta ``athlete_id``, ``athlete_ids`` (lista) o ``athlete_ids_csv``.
    Si no se especifica ninguno, procesa todos los atletas con tokens.
    """
    if not _internal_request_authorized():
        return {"error": "Unauthorized internal pipeline trigger."}, 401

    data = request.get_json(silent=True) or {}
    try:
        athlete_ids_csv = _parse_athlete_ids_csv(data)
        report = asyncio.run(sdk_client.run_index_wiki(athlete_ids_csv=athlete_ids_csv))
        return report, 200
    except ValidationError as exc:
        return {"error": str(exc)}, 400
    except Exception as exc:  # noqa: BLE001
        return {"error": "Index wiki pipeline execution failed.", "details": str(exc)}, 500


@app.get("/pipeline/run/<run_id>")
def get_pipeline_run_endpoint(run_id: str) -> tuple[dict[str, Any], int]:
    if not _internal_request_authorized():
        return {"error": "Unauthorized."}, 401

    try:
        return asyncio.run(sdk_client.get_pipeline_run(run_id=run_id.strip())), 200
    except NotFoundError:
        return {"error": "run_not_found", "run_id": run_id}, 404
    except ValidationError as exc:
        return {"error": str(exc)}, 400
    except Exception as exc:  # noqa: BLE001
        return {"error": "Failed to get pipeline run.", "details": str(exc)}, 500


@app.get("/pipeline/runs")
def list_pipeline_runs_endpoint() -> tuple[dict[str, Any], int]:
    if not _internal_request_authorized():
        return {"error": "Unauthorized."}, 401

    limit = max(1, min(_to_int(request.args.get("limit"), 20), 100))
    stage = (request.args.get("stage") or "").strip()

    try:
        payload = asyncio.run(sdk_client.list_pipeline_runs(limit=limit, stage=stage))
        return payload, 200
    except ValidationError as exc:
        return {"error": str(exc)}, 400
    except Exception as exc:  # noqa: BLE001
        return {"error": "Failed to list pipeline runs.", "details": str(exc)}, 500


@app.post("/internal/pipeline/stage")
@app.post("/pipeline/stage")
def run_pipeline_stage_endpoint() -> tuple[dict[str, Any], int]:
    if not _internal_request_authorized():
        return {"error": "Unauthorized internal pipeline trigger."}, 401

    data = request.get_json(silent=True) or {}
    stage = (data.get("stage") or "").strip().lower()
    if not stage:
        return {"error": "Field 'stage' is required."}, 400

    try:
        report = asyncio.run(sdk_client.run_pipeline_stage(
            stage=stage,
            athlete_ids_csv=_parse_athlete_ids_csv(data),
            latest_limit=max(1, min(_to_int(data.get("latest_limit"), 10), 200)),
            max_activities=max(1, min(_to_int(data.get("max_activities"), 100), 500)),
            daily_run_id=str(data.get("daily_run_id") or "").strip(),
        ))
        return report, 200
    except ValidationError as exc:
        if "Unsupported stage" in str(exc):
            return {
                "error": "Unsupported stage.",
                "supported_stages": [
                    "ingestion",
                    "research_wiki",
                ],
            }, 400
        return {"error": str(exc)}, 400
    except Exception as exc:  # noqa: BLE001
        return {"error": "Pipeline stage execution failed.", "details": str(exc)}, 500


@app.get("/pipeline/activities-runs")
def list_activities_runs_endpoint() -> tuple[dict[str, Any], int]:
    """Lista las runs de indexación de actividades Strava para un atleta.

    Consulta la colección Firestore ``activities_runs`` (fallback a storage
    local) y devuelve cada actividad con su ``status`` (``queued`` |
    ``running`` | ``success`` | ``failed``) y los campos básicos para
    mostrar en frontend.
    """
    athlete_id = _to_optional_int(request.args.get("athlete_id"))
    if athlete_id is None or athlete_id <= 0:
        return {"error": "Query param 'athlete_id' is required."}, 400

    limit = max(1, min(_to_int(request.args.get("limit"), 20), 100))

    try:
        return asyncio.run(sdk_client.list_activity_runs(athlete_id=athlete_id, limit=limit)), 200
    except ValidationError as exc:
        return {"error": str(exc)}, 400
    except Exception as exc:  # noqa: BLE001
        logger.exception("list_activity_runs failed for athlete %s", athlete_id)
        return {"error": "Failed to list activity runs.", "details": str(exc)}, 500


@app.get("/pipeline/indexed-activities")
def list_indexed_activities_endpoint() -> tuple[dict[str, Any], int]:
    """Lista sólo las actividades ya indexadas (status ``success`` o
    ``partial_success``) para un atleta, ordenadas por fecha desc.
    """
    athlete_id = _to_optional_int(request.args.get("athlete_id"))
    if athlete_id is None or athlete_id <= 0:
        return {"error": "Query param 'athlete_id' is required."}, 400

    limit = max(1, min(_to_int(request.args.get("limit"), 20), 100))

    try:
        return asyncio.run(sdk_client.list_indexed_activities(
            athlete_id=athlete_id,
            limit=limit,
        )), 200
    except ValidationError as exc:
        return {"error": str(exc)}, 400
    except Exception as exc:  # noqa: BLE001
        logger.exception("list_indexed_activities failed for athlete %s", athlete_id)
        return {"error": "Failed to list indexed activities.", "details": str(exc)}, 500


@app.get("/pipeline/indexing-status")
def get_indexing_status() -> tuple[dict[str, Any], int]:
    athlete_id = _to_optional_int(request.args.get("athlete_id"))
    if athlete_id is None or athlete_id <= 0:
        return {"error": "Query param 'athlete_id' is required."}, 400

    try:
        return asyncio.run(sdk_client.get_indexing_status(athlete_id=athlete_id)), 200
    except ValidationError as exc:
        return {"error": str(exc)}, 400
    except Exception as exc:  # noqa: BLE001
        return {"error": "Failed to get indexing status.", "details": str(exc)}, 500


@app.post("/chat/wiki")
def chat_wiki_agent() -> Response | tuple[dict[str, Any], int]:
    data = request.get_json(silent=True) or {}

    question = (data.get("message") or data.get("question") or "").strip()
    if not question:
        return {"error": "Field 'message' or 'question' must be a non-empty string."}, 400

    athlete_id = _to_optional_int(data.get("athlete_id") or data.get("strava_athlete_id"))
    if athlete_id is None or athlete_id <= 0:
        return {"error": "Field 'athlete_id' is required."}, 400

    model_raw = data.get("model")
    model_name = model_raw.strip() if isinstance(model_raw, str) and model_raw.strip() else None

    stream_param = data.get("stream", False)
    if isinstance(stream_param, str):
        stream = stream_param.lower() in ("true", "1", "yes")
    else:
        stream = bool(stream_param)

    if stream:
        run_streaming_fn = lambda q, _agent: sdk_client.chat_wiki_stream(
            question=q,
            athlete_id=int(athlete_id),
            model_name=model_name,
        )
        return Response(
            stream_with_context(
                _stream_generator(question, None, run_streaming_fn)
            ),
            content_type="text/event-stream",
            headers={
                "X-Accel-Buffering": "no",
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
            },
        )

    try:
        result = asyncio.run(sdk_client.chat_wiki(
            question=question,
            athlete_id=int(athlete_id),
            model_name=model_name,
        ))
        payload = result.to_payload()
        payload["athlete_id"] = athlete_id
        return jsonify(payload)
    except ValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    except NotFoundError as exc:
        return jsonify({"error": "wiki_not_found", "details": str(exc)}), 404
    except SDKError as exc:
        return jsonify({"error": str(exc)}), 500
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": "Wiki chat agent setup failed.", "details": str(exc)}), 500


@app.route("/vector_stores", methods=["GET", "POST", "DELETE"])
@app.post("/add_to_vs")
@app.post("/strava/weekly-summary")
@app.get("/search_vs")
@app.post("/vectorize")
@app.get("/get_vs_file_details")
@app.get("/get_vs_file_content")
@app.delete("/delete_vs_file")
def deprecated_legacy_routes() -> tuple[dict[str, Any], int]:
    return {
        "error": "Endpoint removed in layered architecture.",
        "use": {
            "chat": ["/chat", "/ask"],
            "query": ["/pipeline/query"],
            "daily_pipeline": ["/pipeline/daily", "/internal/pipeline/daily"],
            "stage_pipeline": ["/pipeline/stage", "/internal/pipeline/stage"],
            "research_wiki_pipeline": ["/pipeline/research-wiki", "/internal/pipeline/research-wiki"],
            "index_wiki_pipeline": ["/pipeline/index-wiki", "/internal/pipeline/index-wiki"],
        },
    }, 410


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    debug = os.environ.get("FLASK_DEBUG", "").strip().lower() in {"1", "true"}
    app.run(host="0.0.0.0", port=port, debug=debug)
