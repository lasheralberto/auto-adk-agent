from __future__ import annotations

from typing import Any, AsyncIterator

from strava_agent_sdk.config import SDKConfig
from strava_agent_sdk.services import (
    AuthService,
    ChatService,
    PipelineService,
    StatusService,
    WikiChatService,
)
from strava_agent_sdk.types import ChatResponse


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
    ) -> None:
        self.config = config or SDKConfig.from_env()
        self.auth = auth_service or AuthService(self.config)
        self.chat_service = chat_service or ChatService()
        self.pipeline = pipeline_service or PipelineService()
        self.status = status_service or StatusService()
        self.wiki_chat_service = wiki_chat_service or WikiChatService()

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
    ) -> ChatResponse:
        return await self.wiki_chat_service.chat(
            question=question,
            athlete_id=athlete_id,
            model_name=model_name,
        )

    async def chat_wiki_stream(
        self,
        *,
        question: str,
        athlete_id: int,
        model_name: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        async for chunk in self.wiki_chat_service.chat_stream(
            question=question,
            athlete_id=athlete_id,
            model_name=model_name,
        ):
            yield chunk
