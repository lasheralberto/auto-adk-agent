from __future__ import annotations

import os
from dataclasses import dataclass, field

_DEFAULT_STRAVA_SCOPE = "read,activity:read_all,profile:read_all"
_DEFAULT_STRAVA_ALLOWED_REDIRECT_URIS = [
    "https://strava-adk-agent-frontend.vercel.app/",
    "https://strava-adk-agent-frontend.vercel.app/auth/strava/callback",
    "http://localhost:5173/",
    "http://localhost:5173/auth/strava/callback",
    "http://127.0.0.1:5173/",
    "http://127.0.0.1:5173/auth/strava/callback",
]


def _to_optional_int(value: str) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(slots=True)
class SDKConfig:
    strava_client_id: int | None = None
    strava_client_secret: str | None = None
    strava_oauth_state_secret: str | None = None
    strava_allowed_redirect_uris: list[str] = field(default_factory=list)
    default_strava_scope: str = _DEFAULT_STRAVA_SCOPE
    oauth_state_ttl_seconds: int = 600

    @classmethod
    def from_env(cls) -> "SDKConfig":
        configured_redirects = _split_csv(os.environ.get("STRAVA_ALLOWED_REDIRECT_URIS", ""))
        return cls(
            strava_client_id=_to_optional_int(os.environ.get("STRAVA_CLIENT_ID", "")),
            strava_client_secret=(os.environ.get("STRAVA_CLIENT_SECRET", "") or "").strip() or None,
            strava_oauth_state_secret=(os.environ.get("STRAVA_OAUTH_STATE_SECRET", "") or "").strip() or None,
            strava_allowed_redirect_uris=configured_redirects or list(_DEFAULT_STRAVA_ALLOWED_REDIRECT_URIS),
            default_strava_scope=(os.environ.get("STRAVA_DEFAULT_SCOPE", _DEFAULT_STRAVA_SCOPE) or _DEFAULT_STRAVA_SCOPE).strip(),
            oauth_state_ttl_seconds=max(1, int(os.environ.get("STRAVA_OAUTH_STATE_TTL_SECONDS", "600") or 600)),
        )
