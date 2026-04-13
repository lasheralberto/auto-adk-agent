from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import urlencode, urlparse

import requests

from agent.tools.pipeline.storage_backend import AthleteStateStore

from strava_agent_sdk.config import SDKConfig
from strava_agent_sdk.errors import ExternalServiceError, ValidationError

_STRAVA_TOKEN_URL = "https://www.strava.com/api/v3/oauth/token"
_STRAVA_AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
_ALLOWED_STRAVA_SCOPES = {
    "read",
    "read_all",
    "profile:read_all",
    "profile:write",
    "activity:read",
    "activity:read_all",
    "activity:write",
}


def _normalize_redirect_uri(value: str) -> str:
    raw_value = (value or "").strip()
    if not raw_value:
        return ""

    parsed = urlparse(raw_value)
    if parsed.scheme.lower() not in {"https", "http"}:
        return ""
    if not parsed.netloc or parsed.fragment:
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


def _normalize_requested_scope(scope: str, *, default_scope: str, default_when_empty: bool = True) -> str:
    requested = [item.strip() for item in (scope or "").split(",") if item.strip()]
    normalized: list[str] = []
    seen: set[str] = set()

    for item in requested:
        if item not in _ALLOWED_STRAVA_SCOPES or item in seen:
            continue
        normalized.append(item)
        seen.add(item)

    if not normalized and default_when_empty:
        return default_scope
    if not normalized:
        return ""
    return ",".join(normalized)


def _state_b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _state_b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}")


class AuthService:
    def __init__(
        self,
        config: SDKConfig | None = None,
        state_store: AthleteStateStore | None = None,
    ) -> None:
        self._config = config or SDKConfig.from_env()
        self._state_store = state_store or AthleteStateStore()

    async def start_auth(self, *, redirect_uri: str, scope: str) -> dict[str, Any]:
        normalized_redirect_uri = _normalize_redirect_uri(redirect_uri)
        if not normalized_redirect_uri:
            raise ValidationError("Field 'redirect_uri' is required and must be a valid URL.")

        normalized_scope = _normalize_requested_scope(
            scope,
            default_scope=self._config.default_strava_scope,
            default_when_empty=False,
        )
        if not (scope or "").strip():
            raise ValidationError("Field 'scope' is required.")
        if not normalized_scope:
            raise ValidationError("Field 'scope' must include at least one allowed scope.")
        if normalized_redirect_uri not in self._allowed_redirect_uris():
            raise ValidationError("redirect_uri is not allowed for this application.")

        client_id = self._require_client_id()
        state = self._create_oauth_state(normalized_redirect_uri)
        auth_url = self._build_auth_url(
            client_id=client_id,
            redirect_uri=normalized_redirect_uri,
            scope=normalized_scope,
            state=state,
        )

        return {
            "auth_url": auth_url,
            "state": state,
            "scope": normalized_scope,
            "redirect_uri": normalized_redirect_uri,
        }

    async def exchange_code(
        self,
        *,
        code: str,
        state: str,
        redirect_uri: str,
        scope: str = "",
    ) -> dict[str, Any]:
        normalized_code = (code or "").strip()
        normalized_state = (state or "").strip()
        normalized_redirect_uri = _normalize_redirect_uri(redirect_uri)
        callback_scope = _normalize_requested_scope(
            scope,
            default_scope=self._config.default_strava_scope,
            default_when_empty=False,
        )

        if not normalized_code:
            raise ValidationError("Field 'code' is required.")
        if not normalized_state:
            raise ValidationError("Field 'state' is required.")
        if not normalized_redirect_uri:
            raise ValidationError("Field 'redirect_uri' is required and must be a valid URL.")
        if normalized_redirect_uri not in self._allowed_redirect_uris():
            raise ValidationError("redirect_uri is not allowed for this application.")

        state_valid, state_error = self._validate_oauth_state(
            state=normalized_state,
            redirect_uri=normalized_redirect_uri,
        )
        if not state_valid:
            raise ValidationError(state_error)

        client_id = self._require_client_id()
        client_secret = self._require_client_secret()

        try:
            token_data = await asyncio.to_thread(
                self._exchange_code_request,
                client_id,
                client_secret,
                normalized_code,
                normalized_redirect_uri,
            )
        except requests.HTTPError as exc:
            details = self._http_error_payload(exc)
            raise ExternalServiceError(f"Strava token exchange failed: {details}") from exc
        except requests.RequestException as exc:
            raise ExternalServiceError(f"Strava token exchange failed: {exc}") from exc

        token_scope = callback_scope or _normalize_requested_scope(
            str(token_data.get("scope") or "").strip(),
            default_scope=self._config.default_strava_scope,
            default_when_empty=False,
        )
        if token_scope:
            token_data = dict(token_data)
            token_data["scope"] = token_scope

        athlete_payload = token_data.get("athlete")
        athlete_id = (
            int(athlete_payload.get("id"))
            if isinstance(athlete_payload, dict) and str(athlete_payload.get("id") or "").strip().isdigit()
            else None
        )
        if athlete_id is not None:
            self._state_store.upsert_tokens(athlete_id, token_data)

        return self._normalize_token_payload(token_data)

    async def refresh_token(
        self,
        *,
        refresh_token: str,
        athlete_id: int | None = None,
    ) -> dict[str, Any]:
        normalized_refresh_token = (refresh_token or "").strip()
        if not normalized_refresh_token:
            raise ValidationError("Field 'refresh_token' is required.")

        client_id = self._require_client_id()
        client_secret = self._require_client_secret()

        try:
            token_data = await asyncio.to_thread(
                self._refresh_token_request,
                client_id,
                client_secret,
                normalized_refresh_token,
            )
        except requests.HTTPError as exc:
            details = self._http_error_payload(exc)
            raise ExternalServiceError(f"Strava token refresh failed: {details}") from exc
        except requests.RequestException as exc:
            raise ExternalServiceError(f"Strava token refresh failed: {exc}") from exc

        payload_athlete = token_data.get("athlete")
        resolved_athlete_id = (
            int(payload_athlete.get("id"))
            if isinstance(payload_athlete, dict) and str(payload_athlete.get("id") or "").strip().isdigit()
            else athlete_id
        )

        if resolved_athlete_id is not None:
            self._state_store.upsert_tokens(int(resolved_athlete_id), token_data)

        return self._normalize_token_payload(token_data)

    def _allowed_redirect_uris(self) -> set[str]:
        normalized: set[str] = set()
        for redirect_uri in self._config.strava_allowed_redirect_uris:
            candidate = _normalize_redirect_uri(redirect_uri)
            if candidate:
                normalized.add(candidate)
        return normalized

    def _require_client_id(self) -> int:
        if self._config.strava_client_id is None:
            raise ValidationError("STRAVA_CLIENT_ID is missing or invalid.")
        return int(self._config.strava_client_id)

    def _require_client_secret(self) -> str:
        secret = (self._config.strava_client_secret or "").strip()
        if not secret:
            raise ValidationError("STRAVA_CLIENT_SECRET is missing.")
        return secret

    def _state_secret(self) -> str:
        configured = (self._config.strava_oauth_state_secret or "").strip()
        if configured:
            return configured
        return self._require_client_secret()

    def _create_oauth_state(self, redirect_uri: str) -> str:
        payload = {
            "iat": int(time.time()),
            "nonce": hashlib.sha256(str(time.time_ns()).encode("utf-8")).hexdigest()[:32],
            "redirect_uri": redirect_uri,
        }
        payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        payload_token = _state_b64encode(payload_bytes)

        signature = hmac.new(
            self._state_secret().encode("utf-8"),
            payload_token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"{payload_token}.{signature}"

    def _validate_oauth_state(self, *, state: str, redirect_uri: str) -> tuple[bool, str]:
        normalized_state = (state or "").strip()
        if "." not in normalized_state:
            return False, "Invalid OAuth state format."

        payload_token, provided_signature = normalized_state.split(".", 1)
        expected_signature = hmac.new(
            self._state_secret().encode("utf-8"),
            payload_token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected_signature, provided_signature):
            return False, "Invalid OAuth state signature."

        try:
            payload_bytes = _state_b64decode(payload_token)
            payload = json.loads(payload_bytes.decode("utf-8"))
        except Exception:
            return False, "Invalid OAuth state payload."

        issued_at = payload.get("iat")
        if not isinstance(issued_at, int):
            return False, "OAuth state timestamp is invalid."
        if (time.time() - issued_at) > int(self._config.oauth_state_ttl_seconds):
            return False, "OAuth state has expired."

        state_redirect_uri = _normalize_redirect_uri(str(payload.get("redirect_uri") or ""))
        if state_redirect_uri != redirect_uri:
            return False, "redirect_uri mismatch for OAuth state."

        return True, ""

    def _build_auth_url(self, *, client_id: int, redirect_uri: str, scope: str, state: str) -> str:
        query = urlencode(
            {
                "client_id": int(client_id),
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "approval_prompt": "auto",
                "scope": scope,
                "state": state,
            }
        )
        return f"{_STRAVA_AUTHORIZE_URL}?{query}"

    @staticmethod
    def _exchange_code_request(
        client_id: int,
        client_secret: str,
        code: str,
        redirect_uri: str,
    ) -> dict[str, Any]:
        response = requests.post(
            _STRAVA_TOKEN_URL,
            data={
                "client_id": int(client_id),
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ExternalServiceError("Unexpected token exchange payload from Strava.")
        return payload

    @staticmethod
    def _refresh_token_request(
        client_id: int,
        client_secret: str,
        refresh_token: str,
    ) -> dict[str, Any]:
        response = requests.post(
            _STRAVA_TOKEN_URL,
            data={
                "client_id": int(client_id),
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ExternalServiceError("Unexpected token refresh payload from Strava.")
        return payload

    @staticmethod
    def _http_error_payload(exc: requests.HTTPError) -> str:
        try:
            if exc.response is not None:
                return exc.response.text or str(exc)
        except Exception:
            pass
        return str(exc)

    @staticmethod
    def _normalize_token_payload(token_data: dict[str, Any]) -> dict[str, Any]:
        return {
            "token_type": token_data.get("token_type"),
            "access_token": token_data.get("access_token"),
            "refresh_token": token_data.get("refresh_token"),
            "expires_at": token_data.get("expires_at"),
            "expires_in": token_data.get("expires_in"),
            "scope": token_data.get("scope"),
            "athlete": token_data.get("athlete") or {},
        }
