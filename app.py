import asyncio
import os
import threading
import time
from typing import Any
from urllib.parse import urlencode

import requests
from flask import Flask, Response, jsonify, request, stream_with_context
from flask_cors import CORS

from agent.app import build_orchestrator
from agent.runner import run_agent, run_agent_streaming
from agent.service.stream_utils import _stream_generator
from agent.tools.pipeline import (
    run_activity_analysis,
    run_daily_pipeline,
    run_daily_summary,
    run_embedding_index,
    run_performance_insight,
    run_query_layer,
    run_strava_ingestion,
    run_wiki_builder,
)
from agent.tools.pipeline.storage_backend import AthleteStateStore

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

_STRAVA_STATE_TTL_SECONDS = 600
_strava_oauth_state_cache: dict[str, float] = {}
_strava_oauth_lock = threading.Lock()


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


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, (int, float)):
        return value != 0
    return default


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


def _prune_expired_oauth_states() -> None:
    now = time.time()
    with _strava_oauth_lock:
        expired_states = [
            state
            for state, created_at in _strava_oauth_state_cache.items()
            if now - created_at > _STRAVA_STATE_TTL_SECONDS
        ]
        for state in expired_states:
            _strava_oauth_state_cache.pop(state, None)


def _build_strava_auth_url(client_id: int, redirect_uri: str, scope: str, state: str) -> str:
    query = urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": scope,
            "state": state,
            "approval_prompt": "force",
        }
    )
    return f"https://www.strava.com/oauth/authorize?{query}"


def _get_strava_client_id() -> int:
    raw_client_id = (os.environ.get("STRAVA_CLIENT_ID") or "").strip()
    if not raw_client_id:
        raise ValueError("STRAVA_CLIENT_ID is not configured in environment")

    try:
        return int(raw_client_id)
    except ValueError as exc:
        raise ValueError("STRAVA_CLIENT_ID must be a valid integer") from exc


def _get_strava_client_secret() -> str:
    client_secret = (os.environ.get("STRAVA_CLIENT_SECRET") or "").strip()
    if not client_secret:
        raise ValueError("STRAVA_CLIENT_SECRET is not configured in environment")
    return client_secret


def _exchange_strava_code(client_id: int, client_secret: str, code: str, redirect_uri: str) -> dict[str, Any]:
    response = requests.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def _refresh_strava_token(client_id: int, client_secret: str, refresh_token: str) -> dict[str, Any]:
    response = requests.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def _internal_request_authorized() -> bool:
    configured_token = (os.environ.get("INTERNAL_PIPELINE_TOKEN") or "").strip()
    if not configured_token:
        return True

    incoming_token = (
        request.headers.get("X-Internal-Token")
        or request.headers.get("Authorization", "").replace("Bearer ", "")
    )
    return incoming_token.strip() == configured_token


def _parse_chat_request(
    data: dict[str, Any],
) -> tuple[
    str | None,
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
    llm_param = (
        data.get("llm_provider")
        or data.get("llm")
        or os.environ.get("LLM_PROVIDER")
        or os.environ.get("LLM")
        or ""
    )
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

    if isinstance(llm_param, str) and "/" in llm_param:
        parsed_provider, model_from_llm = llm_param.split("/", 1)
        llm_provider = parsed_provider
        if model_from_llm and not model:
            model = model_from_llm.strip()
    else:
        llm_provider = llm_param

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
        llm_provider,
        model_name,
        stream,
        top_k,
        normalized_target_date,
        normalized_access_token,
        athlete_id,
        normalized_response_format,
        normalized_planner_mode,
    )


def _normalize_chat_result(result: object) -> dict[str, object]:
    if isinstance(result, dict):
        response_text = result.get("response")
        if response_text is None:
            response_text = result.get("message")
        if response_text is None:
            response_text = result.get("answer")

        tool_calls = result.get("tool_calls")
        if not isinstance(tool_calls, list):
            tool_calls = []

        normalized_result: dict[str, object] = {
            "response": str(response_text) if response_text is not None else "",
            "tool_calls": tool_calls,
        }

        structured = result.get("structured")
        if isinstance(structured, dict):
            normalized_result["structured"] = structured

        api_version = result.get("api_version")
        if isinstance(api_version, str) and api_version.strip():
            normalized_result["api_version"] = api_version.strip()

        return normalized_result

    return {
        "response": str(result),
        "tool_calls": [],
    }


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

    state_store = AthleteStateStore()
    athletes = state_store.list_athletes_with_tokens()

    normalized: list[dict[str, Any]] = []
    for athlete in athletes:
        profile = athlete.get("profile") if isinstance(athlete.get("profile"), dict) else {}
        normalized.append(
            {
                "athlete_id": athlete.get("athlete_id"),
                "firstname": profile.get("firstname"),
                "lastname": profile.get("lastname"),
                "country": profile.get("country"),
                "last_sync_epoch": athlete.get("last_sync_epoch"),
                "last_sync_status": athlete.get("last_sync_status"),
                "last_indexed_date": athlete.get("last_indexed_date"),
                "token_updated_at": athlete.get("token_updated_at"),
            }
        )

    return {"data": normalized, "count": len(normalized), "state_mode": state_store.mode}, 200


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
        payload = run_query_layer(
            question=question,
            athlete_id=athlete_id,
            top_k=top_k,
            target_date=target_date,
        )
        return payload, 200
    except Exception as exc:  # noqa: BLE001
        return {"error": "Query layer execution failed.", "details": str(exc)}, 500


@app.post("/chat")
@app.post("/ask")
def chat_agent() -> Response | tuple[dict[str, Any], int]:
    data = request.get_json(silent=True) or {}
    (
        question,
        llm_provider,
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

    if not isinstance(llm_provider, str) or not llm_provider.strip():
        return {"error": "Field 'llm_provider' must be a non-empty string (for example 'openai/gpt-4o')."}, 400

    if strava_athlete_id is None or strava_athlete_id <= 0:
        return {
            "error": "Field 'strava_athlete_id' or 'athlete_id' is required for multi-athlete retrieval."
        }, 400

    try:
        retrieval_payload = run_query_layer(
            question=question.strip(),
            athlete_id=int(strava_athlete_id),
            top_k=top_k,
            target_date=query_target_date,
        )
        retrieval_context = str(retrieval_payload.get("context") or "").strip()
        retrieval_hits = retrieval_payload.get("hits")
        if not isinstance(retrieval_hits, list):
            retrieval_hits = []

        augmented_question = question.strip()

        if strava_access_token:
            athlete_hint = f"ID del atleta autenticado: {strava_athlete_id}.\n"
            auth_context = (
                "Contexto de autenticacion Strava para esta sesion:\n"
                "- access_token disponible para esta consulta.\n"
                f"- access_token: {strava_access_token}\n"
                f"{athlete_hint}"
                "- Usa este token solo si una tool lo necesita.\n"
                "- No reveles ni repitas el token al usuario.\n\n"
            )
            augmented_question = f"{auth_context}{augmented_question}"

        if retrieval_context:
            augmented_question = (
                "Contexto RAG recuperado por Query Layer:\n"
                "### COMIENZO DEL CONTEXTO ###\n"
                f"{retrieval_context}\n"
                "### FIN DEL CONTEXTO ###\n\n"
                f"Pregunta del usuario: {question.strip()}\n"
                "Responde con base en el contexto recuperado. "
                "Si no hay evidencia suficiente, dilo y sugiere correr pipeline diario."
            )
        else:
            augmented_question = (
                f"Pregunta del usuario: {question.strip()}\n"
                "No se encontro contexto en el indice para este atleta. "
                "Responde con esta limitacion y sugiere ejecutar pipeline diario."
            )

        augmented_question = (
            f"Contexto de atleta para herramientas: athlete_id={strava_athlete_id}.\n"
            f"{augmented_question}"
        )

        orchestrator = build_orchestrator(
            llm_provider=llm_provider.strip().lower(),
            model_name=model_name_to_use,
            planner_mode=planner_mode,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": "Chat preparation failed.", "details": str(exc)}), 500

    if stream:
        run_streaming_with_format = lambda current_question, current_agent: run_agent_streaming(
            current_question,
            current_agent,
            response_format=response_format,
        )
        return Response(
            stream_with_context(
                _stream_generator(augmented_question, orchestrator, run_streaming_with_format)
            ),
            content_type="text/event-stream",
            headers={
                "X-Accel-Buffering": "no",
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
            },
        )

    result = _normalize_chat_result(
        asyncio.run(
            run_agent(
                augmented_question,
                orchestrator,
                response_format=response_format,
            )
        )
    )

    result["retrieval_hits"] = retrieval_hits
    result["query_mode"] = retrieval_payload.get("mode")

    return jsonify(result)


@app.get("/auth/strava/start")
def start_strava_auth() -> tuple[dict[str, Any], int]:
    redirect_uri = (request.args.get("redirect_uri") or "").strip()
    scope = (request.args.get("scope") or "read,activity:read_all,profile:read_all").strip()

    if not redirect_uri:
        return {"error": "Query param 'redirect_uri' is required."}, 400

    try:
        client_id = _get_strava_client_id()
    except ValueError as exc:
        return {"error": str(exc)}, 500

    _prune_expired_oauth_states()
    state = os.urandom(24).hex()
    with _strava_oauth_lock:
        _strava_oauth_state_cache[state] = time.time()

    auth_url = _build_strava_auth_url(
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=scope,
        state=state,
    )

    return {
        "auth_url": auth_url,
        "state": state,
        "scope": scope,
        "redirect_uri": redirect_uri,
    }, 200


@app.post("/auth/strava/exchange")
def exchange_strava_auth_code() -> tuple[dict[str, Any], int]:
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()
    state = (data.get("state") or "").strip()
    redirect_uri = (data.get("redirect_uri") or "").strip()

    if not code:
        return {"error": "Field 'code' is required."}, 400
    if not state:
        return {"error": "Field 'state' is required."}, 400
    if not redirect_uri:
        return {"error": "Field 'redirect_uri' is required."}, 400

    _prune_expired_oauth_states()
    with _strava_oauth_lock:
        state_created_at = _strava_oauth_state_cache.pop(state, None)

    if state_created_at is None:
        return {"error": "Invalid or expired OAuth state. Please login again."}, 400

    try:
        client_id = _get_strava_client_id()
        client_secret = _get_strava_client_secret()
        token_data = _exchange_strava_code(
            client_id=client_id,
            client_secret=client_secret,
            code=code,
            redirect_uri=redirect_uri,
        )
    except requests.HTTPError as exc:
        error_payload = ""
        try:
            error_payload = exc.response.text
        except Exception:  # noqa: BLE001
            pass
        return {
            "error": "Strava token exchange failed.",
            "details": error_payload or str(exc),
        }, 400
    except ValueError as exc:
        return {"error": str(exc)}, 500
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}, 500

    athlete_payload = token_data.get("athlete")
    athlete_id = _to_optional_int(athlete_payload.get("id")) if isinstance(athlete_payload, dict) else None
    if athlete_id is not None:
        AthleteStateStore().upsert_tokens(athlete_id, token_data)

    return {
        "token_type": token_data.get("token_type"),
        "access_token": token_data.get("access_token"),
        "refresh_token": token_data.get("refresh_token"),
        "expires_at": token_data.get("expires_at"),
        "expires_in": token_data.get("expires_in"),
        "scope": token_data.get("scope"),
        "athlete": token_data.get("athlete") or {},
    }, 200


@app.post("/auth/strava/refresh")
def refresh_strava_auth_token() -> tuple[dict[str, Any], int]:
    data = request.get_json(silent=True) or {}
    refresh_token = (data.get("refresh_token") or "").strip()

    if not refresh_token:
        return {"error": "Field 'refresh_token' is required."}, 400

    try:
        client_id = _get_strava_client_id()
        client_secret = _get_strava_client_secret()
        token_data = _refresh_strava_token(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
        )
    except requests.HTTPError as exc:
        error_payload = ""
        try:
            error_payload = exc.response.text
        except Exception:  # noqa: BLE001
            pass
        return {
            "error": "Strava token refresh failed.",
            "details": error_payload or str(exc),
        }, 400
    except ValueError as exc:
        return {"error": str(exc)}, 500
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}, 500

    athlete_payload = token_data.get("athlete")
    athlete_id = _to_optional_int(athlete_payload.get("id")) if isinstance(athlete_payload, dict) else None
    if athlete_id is None:
        athlete_id = _to_optional_int(data.get("strava_athlete_id"))

    if athlete_id is not None:
        AthleteStateStore().upsert_tokens(athlete_id, token_data)

    return {
        "token_type": token_data.get("token_type"),
        "access_token": token_data.get("access_token"),
        "refresh_token": token_data.get("refresh_token"),
        "expires_at": token_data.get("expires_at"),
        "expires_in": token_data.get("expires_in"),
        "scope": token_data.get("scope"),
        "athlete": token_data.get("athlete") or {},
    }, 200


@app.post("/internal/pipeline/daily")
@app.post("/pipeline/daily")
def run_daily_pipeline_endpoint() -> tuple[dict[str, Any], int]:
    if not _internal_request_authorized():
        return {"error": "Unauthorized internal pipeline trigger."}, 401

    data = request.get_json(silent=True) or {}
    athlete_ids_csv = _parse_athlete_ids_csv(data)
    target_date = data.get("target_date") if isinstance(data.get("target_date"), str) else ""
    lookback_days = max(1, min(_to_int(data.get("lookback_days"), 7), 30))
    window_days = max(2, min(_to_int(data.get("window_days"), 14), 60))

    try:
        report = run_daily_pipeline(
            athlete_ids_csv=athlete_ids_csv,
            target_date=target_date,
            lookback_days=lookback_days,
            window_days=window_days,
        )
        return report, 200
    except Exception as exc:  # noqa: BLE001
        return {"error": "Daily pipeline execution failed.", "details": str(exc)}, 500


@app.post("/internal/pipeline/stage")
@app.post("/pipeline/stage")
def run_pipeline_stage_endpoint() -> tuple[dict[str, Any], int]:
    if not _internal_request_authorized():
        return {"error": "Unauthorized internal pipeline trigger."}, 401

    data = request.get_json(silent=True) or {}
    stage = (data.get("stage") or "").strip().lower()
    if not stage:
        return {"error": "Field 'stage' is required."}, 400

    athlete_ids_csv = _parse_athlete_ids_csv(data)
    target_date = data.get("target_date") if isinstance(data.get("target_date"), str) else ""

    try:
        if stage == "ingestion":
            lookback_days = max(1, min(_to_int(data.get("lookback_days"), 7), 30))
            return run_strava_ingestion(athlete_ids_csv=athlete_ids_csv, lookback_days=lookback_days), 200

        if stage == "activity_analysis":
            return run_activity_analysis(athlete_ids_csv=athlete_ids_csv, target_date=target_date), 200

        if stage == "daily_summary":
            return run_daily_summary(athlete_ids_csv=athlete_ids_csv, target_date=target_date), 200

        if stage == "performance_insight":
            window_days = max(2, min(_to_int(data.get("window_days"), 14), 60))
            return (
                run_performance_insight(
                    athlete_ids_csv=athlete_ids_csv,
                    target_date=target_date,
                    window_days=window_days,
                ),
                200,
            )

        if stage == "wiki_builder":
            return run_wiki_builder(athlete_ids_csv=athlete_ids_csv, target_date=target_date), 200

        if stage == "embedding":
            force_reindex = _coerce_bool(data.get("force_reindex"), default=False)
            return (
                run_embedding_index(
                    athlete_ids_csv=athlete_ids_csv,
                    target_date=target_date,
                    force_reindex=force_reindex,
                ),
                200,
            )
    except Exception as exc:  # noqa: BLE001
        return {"error": "Pipeline stage execution failed.", "details": str(exc)}, 500

    return {
        "error": "Unsupported stage.",
        "supported_stages": [
            "ingestion",
            "activity_analysis",
            "daily_summary",
            "performance_insight",
            "wiki_builder",
            "embedding",
        ],
    }, 400


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
        },
    }, 410


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    debug = os.environ.get("FLASK_DEBUG", "").strip().lower() in {"1", "true"}
    app.run(host="0.0.0.0", port=port, debug=debug)
