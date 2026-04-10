import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

logger = logging.getLogger(__name__)

import requests
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request, stream_with_context
from flask_cors import CORS

_BASE_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=_BASE_DIR / ".env", override=False)

from agent.app import build_orchestrator
from agent.runner import run_agent, run_agent_streaming
from agent.service.stream_utils import _stream_generator
from agent.tools.pipeline import (
    research_wiki_pipeline,
    run_daily_pipeline,
    run_pinecone_indexing,
    run_query_layer,
    run_strava_ingestion,
    rag_wiki_pipeline,
)
from agent.tools.pipeline.storage_backend import AthleteStateStore
from agent.agents.wiki_research_chat_agent import (
    build_wiki_research_chat_agent,
    read_wiki_research_md,
)
from agent.config.config import get_llm_provider

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
_DEFAULT_STRAVA_SCOPE = "read,activity:read_all,profile:read_all"
_STRAVA_TOKEN_URL = "https://www.strava.com/api/v3/oauth/token"
_ALLOWED_STRAVA_SCOPES = {
    "read",
    "read_all",
    "profile:read_all",
    "profile:write",
    "activity:read",
    "activity:read_all",
    "activity:write",
}
_DEFAULT_STRAVA_ALLOWED_REDIRECT_URIS = [
    "https://strava-adk-agent-frontend.vercel.app/",
    "https://strava-adk-agent-frontend.vercel.app/auth/strava/callback",
    "http://localhost:5173/",
    "http://localhost:5173/auth/strava/callback",
    "http://127.0.0.1:5173/",
    "http://127.0.0.1:5173/auth/strava/callback",
]


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


def _normalize_redirect_uri(value: str) -> str:
    raw_value = value.strip()
    if not raw_value:
        return ""

    parsed = urlparse(raw_value)
    if parsed.scheme.lower() not in {"https", "http"}:
        return ""
    if not parsed.netloc:
        return ""
    if parsed.fragment:
        return ""

    host = (parsed.hostname or "").lower()
    if not host:
        return ""

    scheme = parsed.scheme.lower()
    if scheme == "http" and host not in {"localhost", "127.0.0.1"}:
        return ""

    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    query = f"?{parsed.query}" if parsed.query else ""

    return f"{scheme}://{host}{port}{path}{query}"


def _allowed_strava_redirect_uris() -> set[str]:
    configured_redirect_uris = [
        candidate.strip()
        for candidate in (os.environ.get("STRAVA_ALLOWED_REDIRECT_URIS") or "").split(",")
        if candidate.strip()
    ]
    source = configured_redirect_uris or _DEFAULT_STRAVA_ALLOWED_REDIRECT_URIS

    allowed: set[str] = set()
    for redirect_uri in source:
        normalized = _normalize_redirect_uri(redirect_uri)
        if normalized:
            allowed.add(normalized)
    return allowed


def _is_allowed_redirect_uri(redirect_uri: str) -> bool:
    return redirect_uri in _allowed_strava_redirect_uris()


def _normalize_requested_strava_scope(scope: str, *, default_when_empty: bool = True) -> str:
    requested = [item.strip() for item in scope.split(",") if item.strip()]
    normalized: list[str] = []
    seen: set[str] = set()

    for item in requested:
        if item not in _ALLOWED_STRAVA_SCOPES or item in seen:
            continue
        normalized.append(item)
        seen.add(item)

    if not normalized and default_when_empty:
        return _DEFAULT_STRAVA_SCOPE

    if not normalized:
        return ""

    return ",".join(normalized)


def _state_b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _state_b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}")


def _get_strava_state_secret() -> str:
    configured_secret = (os.environ.get("STRAVA_OAUTH_STATE_SECRET") or "").strip()
    if configured_secret:
        return configured_secret
    return _get_strava_client_secret()


def _create_strava_oauth_state(redirect_uri: str) -> str:
    payload = {
        "iat": int(time.time()),
        "nonce": secrets.token_urlsafe(24),
        "redirect_uri": redirect_uri,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

    secret_bytes = _get_strava_state_secret().encode("utf-8")
    signature = hmac.new(secret_bytes, payload_bytes, hashlib.sha256).digest()
    return f"{_state_b64encode(payload_bytes)}.{_state_b64encode(signature)}"


def _validate_strava_oauth_state(state: str, redirect_uri: str) -> tuple[bool, str]:
    parts = state.split(".")
    if len(parts) != 2:
        return False, "Invalid OAuth state format. Please login again."

    payload_segment, signature_segment = parts
    try:
        payload_bytes = _state_b64decode(payload_segment)
        provided_signature = _state_b64decode(signature_segment)
    except Exception:  # noqa: BLE001
        return False, "Invalid OAuth state encoding. Please login again."

    secret_bytes = _get_strava_state_secret().encode("utf-8")
    expected_signature = hmac.new(secret_bytes, payload_bytes, hashlib.sha256).digest()
    if not hmac.compare_digest(provided_signature, expected_signature):
        return False, "Invalid OAuth state signature. Please login again."

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return False, "Invalid OAuth state payload. Please login again."

    if not isinstance(payload, dict):
        return False, "Invalid OAuth state payload. Please login again."

    state_redirect_uri = _normalize_redirect_uri(str(payload.get("redirect_uri") or ""))
    if state_redirect_uri != redirect_uri:
        return False, "OAuth state does not match redirect_uri. Please login again."

    issued_at = _to_optional_int(payload.get("iat"))
    if issued_at is None:
        return False, "Invalid OAuth state timestamp. Please login again."

    now = int(time.time())
    if issued_at > now + 60:
        return False, "Invalid OAuth state timestamp. Please login again."
    if now - issued_at > _STRAVA_STATE_TTL_SECONDS:
        return False, "Invalid or expired OAuth state. Please login again."

    nonce = payload.get("nonce")
    if not isinstance(nonce, str) or len(nonce) < 16:
        return False, "Invalid OAuth state nonce. Please login again."

    return True, ""


def _build_strava_auth_url(client_id: int, redirect_uri: str, scope: str, state: str) -> str:
    query_payload: dict[str, Any] = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
    }

    approval_prompt = (os.environ.get("STRAVA_APPROVAL_PROMPT") or "").strip().lower()
    if approval_prompt in {"auto", "force"}:
        query_payload["approval_prompt"] = approval_prompt

    query = urlencode(query_payload)
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
        _STRAVA_TOKEN_URL,
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
        _STRAVA_TOKEN_URL,
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


def _internal_pipeline_base_url() -> str:
    configured_url = (os.environ.get("INTERNAL_PIPELINE_BASE_URL") or "").strip().rstrip("/")
    if configured_url:
        return configured_url
    port = (os.environ.get("PORT") or "8080").strip() or "8080"
    return f"http://127.0.0.1:{port}"


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
            AthleteStateStore().upsert_tokens(
                int(strava_athlete_id),
                {
                    "access_token": strava_access_token,
                    "athlete": {"id": int(strava_athlete_id)},
                },
            )
            auth_context = (
                "Contexto de autenticacion Strava para esta sesion:\n"
                f"- athlete_id autenticado: {strava_athlete_id}\n"
                "- Token disponible de forma interna para herramientas backend.\n"
                "- Nunca reveles credenciales ni solicites secretos al usuario.\n\n"
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
    redirect_uri = _normalize_redirect_uri((request.args.get("redirect_uri") or "").strip())
    requested_scope = (request.args.get("scope") or "").strip()
    scope = _normalize_requested_strava_scope(
        requested_scope,
        default_when_empty=False,
    )

    if not redirect_uri:
        return {"error": "Query param 'redirect_uri' is required and must be a valid URL."}, 400
    if not requested_scope:
        return {"error": "Query param 'scope' is required."}, 400
    if not scope:
        return {"error": "Query param 'scope' must include at least one allowed scope."}, 400
    if not _is_allowed_redirect_uri(redirect_uri):
        return {"error": "redirect_uri is not allowed for this application."}, 400

    try:
        client_id = _get_strava_client_id()
        state = _create_strava_oauth_state(redirect_uri)
    except ValueError as exc:
        return {"error": str(exc)}, 500

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
    redirect_uri = _normalize_redirect_uri((data.get("redirect_uri") or "").strip())
    callback_scope = _normalize_requested_strava_scope(
        (data.get("scope") or "").strip(),
        default_when_empty=False,
    )

    if not code:
        return {"error": "Field 'code' is required."}, 400
    if not state:
        return {"error": "Field 'state' is required."}, 400
    if not redirect_uri:
        return {"error": "Field 'redirect_uri' is required and must be a valid URL."}, 400
    if not _is_allowed_redirect_uri(redirect_uri):
        return {"error": "redirect_uri is not allowed for this application."}, 400

    try:
        state_valid, state_error = _validate_strava_oauth_state(state=state, redirect_uri=redirect_uri)
    except ValueError as exc:
        return {"error": str(exc)}, 500

    if not state_valid:
        return {"error": state_error}, 400

    try:
        client_id = _get_strava_client_id()
        client_secret = _get_strava_client_secret()
        token_data = _exchange_strava_code(
            client_id=client_id,
            client_secret=client_secret,
            code=code,
            redirect_uri=redirect_uri,
        )
        token_scope = callback_scope or _normalize_requested_strava_scope(
            str(token_data.get("scope") or "").strip(),
            default_when_empty=False,
        )
        if token_scope:
            token_data = dict(token_data)
            token_data["scope"] = token_scope
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
        athlete_id = _to_optional_int(data.get("strava_athlete_id") or data.get("athlete_id"))

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

    try:
        report = run_daily_pipeline(
            athlete_ids_csv=athlete_ids_csv,
            target_date=target_date,
        )

        dispatch_payload = {
            "athlete_id": _to_optional_int(data.get("athlete_id")),
            "athlete_ids_csv": athlete_ids_csv,
            "target_date": str(report.get("target_date") or target_date),
            "daily_run_id": str(report.get("run_id") or ""),
        }
        try:
            report["steps"]["research_wiki_dispatch"] = _dispatch_research_wiki_async(dispatch_payload)
        except Exception as exc:  # noqa: BLE001
            report["steps"]["research_wiki_dispatch"] = {
                "status": "failed",
                "error": str(exc),
            }

        return report, 200
    except Exception as exc:  # noqa: BLE001
        return {"error": "Daily pipeline execution failed.", "details": str(exc)}, 500


@app.post("/internal/pipeline/research-wiki")
@app.post("/pipeline/research-wiki")
def run_research_wiki_endpoint() -> tuple[dict[str, Any], int]:
    if not _internal_request_authorized():
        return {"error": "Unauthorized internal pipeline trigger."}, 401

    data = request.get_json(silent=True) or {}
    athlete_ids_csv = _parse_athlete_ids_csv(data)
    target_date = data.get("target_date") if isinstance(data.get("target_date"), str) else ""
    daily_run_id = str(data.get("daily_run_id") or "").strip()

    try:
        report = research_wiki_pipeline(
            athlete_ids_csv=athlete_ids_csv,
            target_date=target_date,
            daily_run_id=daily_run_id,
        )
        return report, 200
    except Exception as exc:  # noqa: BLE001
        return {"error": "Research wiki pipeline execution failed.", "details": str(exc)}, 500


@app.get("/pipeline/run/<run_id>")
def get_pipeline_run_endpoint(run_id: str) -> tuple[dict[str, Any], int]:
    if not _internal_request_authorized():
        return {"error": "Unauthorized."}, 401

    state_store = AthleteStateStore()
    run = state_store.get_pipeline_run(run_id.strip())
    if run is None:
        return {"error": "run_not_found", "run_id": run_id}, 404
    return run, 200


@app.get("/pipeline/runs")
def list_pipeline_runs_endpoint() -> tuple[dict[str, Any], int]:
    if not _internal_request_authorized():
        return {"error": "Unauthorized."}, 401

    limit = max(1, min(_to_int(request.args.get("limit"), 20), 100))
    stage = (request.args.get("stage") or "").strip()

    state_store = AthleteStateStore()
    runs = state_store.list_pipeline_runs(limit=limit)

    if stage:
        runs = [r for r in runs if r.get("stage") == stage]

    return {"runs": runs, "count": len(runs)}, 200


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

        if stage == "pinecone_indexing":
            return run_pinecone_indexing(athlete_ids_csv=athlete_ids_csv, target_date=target_date), 200

        if stage == "rag_wiki":
            return rag_wiki_pipeline(athlete_ids_csv=athlete_ids_csv, target_date=target_date), 200

        if stage == "research_wiki":
            window_days = max(2, min(_to_int(data.get("window_days"), 14), 60))
            daily_run_id = str(data.get("daily_run_id") or "").strip()
            return research_wiki_pipeline(
                athlete_ids_csv=athlete_ids_csv,
                target_date=target_date,
                window_days=window_days,
                daily_run_id=daily_run_id,
            ), 200

    except Exception as exc:  # noqa: BLE001
        return {"error": "Pipeline stage execution failed.", "details": str(exc)}, 500

    return {
        "error": "Unsupported stage.",
        "supported_stages": [
            "ingestion",
            "pinecone_indexing",
            "rag_wiki",
            "research_wiki",
        ],
    }, 400


@app.get("/pipeline/indexing-status")
def get_indexing_status() -> tuple[dict[str, Any], int]:
    athlete_id = _to_optional_int(request.args.get("athlete_id"))
    if athlete_id is None or athlete_id <= 0:
        return {"error": "Query param 'athlete_id' is required."}, 400

    state_store = AthleteStateStore()
    today = datetime.now(timezone.utc).date().isoformat()
    athlete_doc = state_store.get_athlete(athlete_id) or {}
    last_indexed = athlete_doc.get("last_indexed_date") or state_store.get_last_indexed_date(athlete_id)
    last_sync_status = athlete_doc.get("last_sync_status")

    return {
        "athlete_id": athlete_id,
        "today": today,
        "last_indexed_date": last_indexed,
        "indexed_today": last_indexed == today,
        "last_sync_status": last_sync_status,
    }, 200


@app.post("/chat/wiki")
def chat_wiki_agent() -> Response | tuple[dict[str, Any], int]:
    data = request.get_json(silent=True) or {}

    question = (data.get("message") or data.get("question") or "").strip()
    if not question:
        return {"error": "Field 'message' or 'question' must be a non-empty string."}, 400

    athlete_id = _to_optional_int(data.get("athlete_id") or data.get("strava_athlete_id"))
    if athlete_id is None or athlete_id <= 0:
        return {"error": "Field 'athlete_id' is required."}, 400

    llm_param = (
        data.get("llm_provider")
        or data.get("llm")
        or os.environ.get("LLM_PROVIDER")
        or os.environ.get("LLM")
        or ""
    )
    if not isinstance(llm_param, str) or not llm_param.strip():
        return {"error": "Field 'llm_provider' must be a non-empty string (e.g. 'google/gemini-2.5-flash')."}, 400

    model_raw = data.get("model")
    model_name = model_raw.strip() if isinstance(model_raw, str) and model_raw.strip() else None

    stream_param = data.get("stream", False)
    if isinstance(stream_param, str):
        stream = stream_param.lower() in ("true", "1", "yes")
    else:
        stream = bool(stream_param)

    wiki_content = read_wiki_research_md(athlete_id)
    if wiki_content is None:
        return {
            "error": "wiki_not_found",
            "details": (
                f"No se encontró research.md para el atleta {athlete_id} "
                "en el bucket wiki. Ejecuta primero la pipeline de investigación."
            ),
        }, 404

    try:
        selected_model = get_llm_provider(llm_provider=llm_param.strip().lower(), model_name=model_name)
        wiki_agent = build_wiki_research_chat_agent(
            selected_model=selected_model,
            wiki_content=wiki_content,
            athlete_id=athlete_id,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": "Wiki chat agent setup failed.", "details": str(exc)}), 500

    if stream:
        run_streaming_fn = lambda q, agent: run_agent_streaming(q, agent)
        return Response(
            stream_with_context(
                _stream_generator(question, wiki_agent, run_streaming_fn)
            ),
            content_type="text/event-stream",
            headers={
                "X-Accel-Buffering": "no",
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
            },
        )

    result = _normalize_chat_result(
        asyncio.run(run_agent(question, wiki_agent))
    )
    result["athlete_id"] = athlete_id
    return jsonify(result)


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
        },
    }, 410


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    debug = os.environ.get("FLASK_DEBUG", "").strip().lower() in {"1", "true"}
    app.run(host="0.0.0.0", port=port, debug=debug)
