from __future__ import annotations

import logging
import os
import secrets
import threading
from collections.abc import Mapping
from typing import Any, AsyncIterator

import requests

from strava_agent_sdk.config import SDKConfig
from strava_agent_sdk.services import (
    AgentDefinitionService,
    AgentsService,
    AuthService,
    ChatService,
    PipelineService,
    SecretsService,
    StatusService,
    WikiChatService,
)
from strava_agent_sdk.types import ChatResponse

logger = logging.getLogger(__name__)


class StravaAgentClient:
    """Async-first SDK client for Strava Agent workflows."""

    def __init__(
        self,
        config: SDKConfig | None = None,
        *,
        auth_service: AuthService | None = None,
        chat_service: ChatService | None = None,
        pipeline_service: PipelineService | None = None,
        status_service: StatusService | None = None,
        wiki_chat_service: WikiChatService | None = None,
        secrets_service: SecretsService | None = None,
        agents_service: AgentsService | None = None,
        agent_definition_service: AgentDefinitionService | None = None,
        gcp_project_id: str | None = None,
        gcp_credentials_path: str | None = None,
    ) -> None:
        self.config = config or SDKConfig.from_env()
        self.auth = auth_service or AuthService(self.config)
        self.chat_service = chat_service or ChatService()
        self.pipeline = pipeline_service or PipelineService()
        self.status = status_service or StatusService()
        self.wiki_chat_service = wiki_chat_service or WikiChatService()
        self.agents = agents_service or AgentsService()
        self.agent_definition = agent_definition_service or AgentDefinitionService()
        self.secrets = secrets_service or SecretsService(
            project_id=gcp_project_id,
            credentials_path=gcp_credentials_path,
        )

    @staticmethod
    def to_int(value: Any, default: int = 0) -> int:
        try:
            if value is None or value == "":
                return default
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def to_optional_int(value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def extract_strava_access_token(
        self,
        payload: dict[str, Any],
        *,
        authorization_header: str = "",
    ) -> str | None:
        for key in ("strava_access_token", "access_token"):
            candidate = payload.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()

        auth_header = authorization_header.strip()
        if auth_header.lower().startswith("bearer "):
            bearer_token = auth_header[7:].strip()
            if bearer_token:
                return bearer_token

        return None

    def parse_athlete_ids_csv(self, data: dict[str, Any]) -> str:
        athlete_ids = data.get("athlete_ids")
        if isinstance(athlete_ids, list):
            normalized_ids: list[str] = []
            for item in athlete_ids:
                athlete_id = self.to_int(item, 0)
                if athlete_id > 0:
                    normalized_ids.append(str(athlete_id))
            return ",".join(normalized_ids)

        athlete_ids_csv = data.get("athlete_ids_csv")
        if isinstance(athlete_ids_csv, str):
            return athlete_ids_csv.strip()

        athlete_id = self.to_optional_int(data.get("athlete_id"))
        if athlete_id is not None and athlete_id > 0:
            return str(athlete_id)

        return ""

    def internal_request_authorized(self, *, headers: Mapping[str, str] | None = None) -> bool:
        configured_token = (os.environ.get("INTERNAL_PIPELINE_TOKEN") or "").strip()
        if not configured_token:
            return True

        incoming_token = ""
        if headers:
            incoming_token = str(headers.get("X-Internal-Token") or "")
            if not incoming_token:
                auth_header = str(headers.get("Authorization") or "")
                if auth_header.lower().startswith("bearer "):
                    incoming_token = auth_header[7:]
                else:
                    incoming_token = auth_header

        return incoming_token.strip() == configured_token

    def internal_pipeline_base_url(self) -> str:
        configured_url = (os.environ.get("INTERNAL_PIPELINE_BASE_URL") or "").strip().rstrip("/")
        if configured_url:
            return configured_url

        port = (os.environ.get("PORT") or "8080").strip() or "8080"
        return f"http://127.0.0.1:{port}"

    def dispatch_pipeline_async(self, *, stage: str, payload: dict[str, Any]) -> dict[str, Any]:
        dispatch_id = secrets.token_hex(8)
        normalized_stage = stage.strip().strip("/")
        endpoint = f"{self.internal_pipeline_base_url()}/internal/pipeline/{normalized_stage}"

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
                        "%s dispatch %s failed: HTTP %s - %s",
                        normalized_stage,
                        dispatch_id,
                        resp.status_code,
                        resp.text[:500],
                    )
            except Exception as exc:  # noqa: BLE001
                logger.exception("%s dispatch %s raised an exception: %s", normalized_stage, dispatch_id, exc)

        worker = threading.Thread(
            target=_run_dispatch,
            name=f"{normalized_stage}-dispatch-{dispatch_id}",
            daemon=True,
        )
        worker.start()

        return {
            "status": "queued",
            "dispatch_id": dispatch_id,
            "endpoint": endpoint,
        }

    def dispatch_index_wiki_async(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.dispatch_pipeline_async(stage="index-wiki", payload=payload)

    def dispatch_research_wiki_async(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.dispatch_pipeline_async(stage="research-wiki", payload=payload)

    def parse_chat_request(
        self,
        *,
        data: dict[str, Any],
        authorization_header: str = "",
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
        strava_access_token = self.extract_strava_access_token(
            data,
            authorization_header=authorization_header,
        )
        strava_athlete_id = data.get("strava_athlete_id")
        response_format = data.get("response_format")
        planner_mode = data.get("planner_mode")

        top_k = max(1, min(self.to_int(data.get("top_k"), 5), 20))
        target_date = data.get("target_date")

        if isinstance(stream_param, str):
            stream = stream_param.lower() in ("true", "1", "yes")
        else:
            stream = bool(stream_param)

        model_name = model.strip() if isinstance(model, str) and model.strip() else None

        athlete_id: int | None = None
        athlete_id_raw = data.get("athlete_id")
        if athlete_id_raw is not None:
            athlete_id = self.to_optional_int(athlete_id_raw)
        elif strava_athlete_id is not None:
            athlete_id = self.to_optional_int(strava_athlete_id)

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

    async def chat(
        self,
        *,
        question: str,
        athlete_id: int,
        model_name: str | None = None,
        top_k: int = 5,
        target_date: str = "",
        access_token: str | None = None,
        response_format: str | None = "plan_react_v1",
        planner_mode: str | None = "full_only",
    ) -> ChatResponse:
        return await self.chat_service.chat(
            question=question,
            athlete_id=athlete_id,
            model_name=model_name,
            top_k=top_k,
            target_date=target_date,
            access_token=access_token,
            response_format=response_format,
            planner_mode=planner_mode,
        )

    async def chat_stream(
        self,
        *,
        question: str,
        athlete_id: int,
        model_name: str | None = None,
        top_k: int = 5,
        target_date: str = "",
        access_token: str | None = None,
        response_format: str | None = "plan_react_v1",
        planner_mode: str | None = "full_only",
    ) -> AsyncIterator[dict[str, Any]]:
        async for chunk in self.chat_service.chat_stream(
            question=question,
            athlete_id=athlete_id,
            model_name=model_name,
            top_k=top_k,
            target_date=target_date,
            access_token=access_token,
            response_format=response_format,
            planner_mode=planner_mode,
        ):
            yield chunk

    async def query_wiki(
        self,
        *,
        question: str,
        athlete_id: int,
        top_k: int = 5,
        target_date: str = "",
    ) -> dict[str, Any]:
        return await self.chat_service.query(
            question=question,
            athlete_id=athlete_id,
            top_k=top_k,
            target_date=target_date,
        )

    async def get_agent_chain_logs(
        self,
        *,
        athlete_id: int,
        page: int = 1,
        page_size: int = 5,
        include_events: bool = False,
    ) -> dict[str, Any]:
        return await self.chat_service.get_agent_chain_logs(
            athlete_id=athlete_id,
            page=page,
            page_size=page_size,
            include_events=include_events,
        )

    async def start_strava_oauth(self, *, redirect_uri: str, scope: str) -> dict[str, Any]:
        return await self.auth.start_auth(redirect_uri=redirect_uri, scope=scope)

    async def exchange_strava_code(
        self,
        *,
        code: str,
        state: str,
        redirect_uri: str,
        scope: str = "",
    ) -> dict[str, Any]:
        return await self.auth.exchange_code(
            code=code,
            state=state,
            redirect_uri=redirect_uri,
            scope=scope,
        )

    async def refresh_strava_token(
        self,
        *,
        refresh_token: str,
        athlete_id: int | None = None,
    ) -> dict[str, Any]:
        return await self.auth.refresh_token(
            refresh_token=refresh_token,
            athlete_id=athlete_id,
        )

    async def run_daily_pipeline(
        self,
        *,
        athlete_id: int | None = None,
        athlete_ids: list[int] | None = None,
        athlete_ids_csv: str = "",
        target_date: str = "",
        latest_limit: int = 10,
    ) -> dict[str, Any]:
        return await self.pipeline.run_daily_pipeline(
            athlete_id=athlete_id,
            athlete_ids=athlete_ids,
            athlete_ids_csv=athlete_ids_csv,
            target_date=target_date,
            latest_limit=latest_limit,
        )

    async def run_research_wiki(
        self,
        *,
        athlete_id: int | None = None,
        athlete_ids: list[int] | None = None,
        athlete_ids_csv: str = "",
        daily_run_id: str = "",
        max_activities: int = 100,
    ) -> dict[str, Any]:
        return await self.pipeline.run_research_wiki(
            athlete_id=athlete_id,
            athlete_ids=athlete_ids,
            athlete_ids_csv=athlete_ids_csv,
            daily_run_id=daily_run_id,
            max_activities=max_activities,
        )

    async def run_pipeline_stage(
        self,
        *,
        stage: str,
        athlete_id: int | None = None,
        athlete_ids: list[int] | None = None,
        athlete_ids_csv: str = "",
        latest_limit: int = 10,
        max_activities: int = 100,
        daily_run_id: str = "",
    ) -> dict[str, Any]:
        return await self.pipeline.run_stage(
            stage=stage,
            athlete_id=athlete_id,
            athlete_ids=athlete_ids,
            athlete_ids_csv=athlete_ids_csv,
            latest_limit=latest_limit,
            max_activities=max_activities,
            daily_run_id=daily_run_id,
        )

    async def run_index_wiki(
        self,
        *,
        athlete_id: int | None = None,
        athlete_ids: list[int] | None = None,
        athlete_ids_csv: str = "",
    ) -> dict[str, Any]:
        return await self.pipeline.run_index_wiki(
            athlete_id=athlete_id,
            athlete_ids=athlete_ids,
            athlete_ids_csv=athlete_ids_csv,
        )

    async def list_athletes(self) -> dict[str, Any]:
        return await self.status.list_athletes()

    async def get_pipeline_run(self, *, run_id: str) -> dict[str, Any]:
        return await self.status.get_pipeline_run(run_id=run_id)

    async def list_pipeline_runs(self, *, limit: int = 20, stage: str = "") -> dict[str, Any]:
        return await self.status.list_pipeline_runs(limit=limit, stage=stage)

    async def list_activity_runs(self, *, athlete_id: int, limit: int = 20) -> dict[str, Any]:
        return await self.status.list_activity_runs(athlete_id=athlete_id, limit=limit)

    async def list_indexed_activities(self, *, athlete_id: int, limit: int = 20) -> dict[str, Any]:
        return await self.status.list_indexed_activities(athlete_id=athlete_id, limit=limit)

    async def get_indexing_status(self, *, athlete_id: int) -> dict[str, Any]:
        return await self.status.get_indexing_status(athlete_id=athlete_id)

    async def chat_wiki(
        self,
        *,
        question: str,
        athlete_id: int,
        model_name: str | None = None,
        agent_id: str | None = None,
    ) -> ChatResponse:
        return await self.wiki_chat_service.chat(
            question=question,
            athlete_id=athlete_id,
            model_name=model_name,
            agent_id=agent_id,
        )

    async def chat_wiki_stream(
        self,
        *,
        question: str,
        athlete_id: int,
        model_name: str | None = None,
        agent_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        async for chunk in self.wiki_chat_service.chat_stream(
            question=question,
            athlete_id=athlete_id,
            model_name=model_name,
            agent_id=agent_id,
        ):
            yield chunk

    async def list_agents(self) -> dict[str, Any]:
        return await self.agents.list_agents()

    async def get_agent(self, *, agent_id: str) -> dict[str, Any]:
        return await self.agents.get_agent(agent_id)

    async def create_agent(
        self,
        *,
        agent_id: str,
        name: str,
        description: str = "",
        instruction_template: str,
        updated_by: str | None = None,
    ) -> dict[str, Any]:
        return await self.agents.create_agent(
            agent_id=agent_id,
            name=name,
            description=description,
            instruction_template=instruction_template,
            updated_by=updated_by,
        )

    async def update_agent(
        self,
        *,
        agent_id: str,
        instruction_template: str,
        name: str | None = None,
        description: str | None = None,
        updated_by: str | None = None,
    ) -> dict[str, Any]:
        return await self.agents.update_agent(
            agent_id,
            instruction_template=instruction_template,
            name=name,
            description=description,
            updated_by=updated_by,
        )

    async def delete_agent(self, *, agent_id: str) -> dict[str, Any]:
        return await self.agents.delete_agent(agent_id)

    async def get_agent_definition(self, *, athlete_id: str | int) -> dict[str, Any]:
        return await self.agent_definition.get_definition(athlete_id)

    async def update_agent_definition(
        self,
        *,
        athlete_id: str | int,
        toml_content: str,
        version: int,
        updated_by: str | None = None,
    ) -> dict[str, Any]:
        return await self.agent_definition.update_definition(
            athlete_id=athlete_id,
            toml_content=toml_content,
            version=version,
            updated_by=updated_by,
        )

    async def delete_agent_definition(self, *, athlete_id: str | int) -> dict[str, Any]:
        return await self.agent_definition.delete_definition(athlete_id)

    async def validate_agent_definition(self, *, toml_content: str) -> dict[str, Any]:
        return await self.agent_definition.validate_definition(toml_content)

    def get_secret(
        self,
        name: str,
        *,
        version: str = "latest",
        use_cache: bool = True,
        default: str | None = None,
    ) -> str:
        return self.secrets.get_secret(
            name,
            version=version,
            use_cache=use_cache,
            default=default,
        )

    def set_secret(self, name: str, value: str) -> str:
        return self.secrets.set_secret(name, value)

    def delete_secret(self, name: str) -> None:
        self.secrets.delete_secret(name)

    def list_secrets(self) -> list[str]:
        return self.secrets.list_secrets()

    def load_secrets_into_env(
        self,
        names: list[str],
        *,
        overwrite: bool = False,
    ) -> dict[str, str]:
        return self.secrets.load_into_env(names, overwrite=overwrite)

    def create_secrets_from_env(
        self,
        env_path: str,
        *,
        names: list[str] | None = None,
        only_update: bool = False,
        grant_access_to: str | list[str] | None = None,
        role: str = "roles/secretmanager.secretAccessor",
    ) -> dict[str, str]:
        return self.secrets.create_secrets_from_env(
            env_path,
            names=names,
            only_update=only_update,
            grant_access_to=grant_access_to,
            role=role,
        )

    def grant_secret_access(
        self,
        name: str,
        member: str,
        *,
        role: str = "roles/secretmanager.secretAccessor",
    ) -> None:
        self.secrets.grant_access(name, member, role=role)

    def check_gcp_auth(self) -> dict[str, str | bool]:
        return self.secrets.check_auth()
