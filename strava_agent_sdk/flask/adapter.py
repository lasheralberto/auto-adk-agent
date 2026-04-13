from __future__ import annotations

import os

from flask import Flask
from flask_cors import CORS

from strava_agent_sdk import StravaAgentClient

_DEFAULT_ALLOWED_ORIGINS = [
    "https://strava-adk-agent-frontend.vercel.app",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


def _build_app(import_name: str) -> Flask:
    app = Flask(import_name)

    configured_origins = [
        origin.strip()
        for origin in (os.environ.get("CORS_ALLOWED_ORIGINS") or "").split(",")
        if origin.strip()
    ]
    allowed_origins = configured_origins or _DEFAULT_ALLOWED_ORIGINS
    CORS(app, origins=allowed_origins, supports_credentials=True)

    return app


class FlaskAdapter:
    def __init__(self, import_name: str, *, sdk_client: StravaAgentClient | None = None) -> None:
        self.sdk_client = sdk_client or StravaAgentClient()
        self.app = _build_app(import_name)

    def create_app(self) -> Flask:
        return self.app

    def register_routes(self) -> None:
        from .routes import register_routes as _register_routes

        _register_routes(self.app, self.sdk_client)

    def register_route(self, route_str: str) -> None:
        from .routes import register_route as _register_route

        _register_route(self.app, self.sdk_client, route_str)


def create_app(import_name: str) -> Flask:
    return _build_app(import_name)


def register_routes(app: Flask, sdk_client: StravaAgentClient) -> None:
    from .routes import register_routes as _register_routes

    _register_routes(app, sdk_client)


def register_route(app: Flask, sdk_client: StravaAgentClient, route_str: str) -> None:
    from .routes import register_route as _register_route

    _register_route(app, sdk_client, route_str)
