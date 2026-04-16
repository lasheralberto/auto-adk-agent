from __future__ import annotations

import asyncio
from typing import Any

from agent.agents.agent_prompts import AgentPromptStore

from strava_agent_sdk.errors import NotFoundError, ValidationError


class AgentsService:
    """Service exposing CRUD over configurable agent prompts."""

    async def list_agents(self) -> dict[str, Any]:
        records = await asyncio.to_thread(AgentPromptStore().list_all)
        return {"agents": records}

    async def get_agent(self, agent_id: str) -> dict[str, Any]:
        normalized = (agent_id or "").strip()
        if not normalized:
            raise ValidationError("agent_id is required.")

        record = await asyncio.to_thread(AgentPromptStore().get, normalized)
        if record is None:
            raise NotFoundError(f"Agent '{normalized}' not found.")
        return record

    async def create_agent(
        self,
        *,
        agent_id: str,
        name: str,
        description: str = "",
        instruction_template: str,
        updated_by: str | None = None,
    ) -> dict[str, Any]:
        normalized = (agent_id or "").strip()
        if not normalized:
            raise ValidationError("agent_id is required.")
        if not isinstance(name, str) or not name.strip():
            raise ValidationError("name is required.")
        if not isinstance(instruction_template, str) or not instruction_template.strip():
            raise ValidationError("instruction_template must be a non-empty string.")

        try:
            return await asyncio.to_thread(
                AgentPromptStore().create,
                normalized,
                name=name.strip(),
                description=description,
                instruction_template=instruction_template,
                updated_by=updated_by,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    async def update_agent(
        self,
        agent_id: str,
        *,
        instruction_template: str,
        name: str | None = None,
        description: str | None = None,
        updated_by: str | None = None,
    ) -> dict[str, Any]:
        normalized = (agent_id or "").strip()
        if not normalized:
            raise ValidationError("agent_id is required.")
        if not isinstance(instruction_template, str) or not instruction_template.strip():
            raise ValidationError("instruction_template must be a non-empty string.")

        try:
            return await asyncio.to_thread(
                AgentPromptStore().set,
                normalized,
                instruction_template,
                name=name,
                description=description,
                updated_by=updated_by,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    async def delete_agent(self, agent_id: str) -> dict[str, Any]:
        normalized = (agent_id or "").strip()
        if not normalized:
            raise ValidationError("agent_id is required.")

        try:
            await asyncio.to_thread(AgentPromptStore().delete, normalized)
            return {"deleted": True, "agent_id": normalized}
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
