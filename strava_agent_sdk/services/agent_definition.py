from __future__ import annotations

import asyncio
from typing import Any

from agent.builder import (
    AgentDefinitionBuilder,
    AgentDefinitionConflictError,
    AgentDefinitionValidationError,
)

from strava_agent_sdk.errors import ConflictError, ValidationError


class AgentDefinitionService:
    """CRUD + validation service for athlete agent-definition TOML files."""

    def __init__(self, *, builder: AgentDefinitionBuilder | None = None) -> None:
        self._builder = builder or AgentDefinitionBuilder()

    @staticmethod
    def _normalize_athlete_id(athlete_id: str | int) -> str:
        normalized = str(athlete_id).strip()
        if not normalized:
            raise ValidationError("athlete_id is required.")
        return normalized

    async def get_definition(self, athlete_id: str | int) -> dict[str, Any]:
        normalized_athlete_id = self._normalize_athlete_id(athlete_id)
        try:
            return await asyncio.to_thread(self._builder.get_definition, normalized_athlete_id)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    async def update_definition(
        self,
        *,
        athlete_id: str | int,
        toml_content: str,
        version: int,
        updated_by: str | None = None,
    ) -> dict[str, Any]:
        normalized_athlete_id = self._normalize_athlete_id(athlete_id)

        if not isinstance(toml_content, str) or not toml_content.strip():
            raise ValidationError("Field 'toml_content' must be a non-empty string.")

        if not isinstance(version, int) or version < 0:
            raise ValidationError("Field 'version' must be an integer >= 0.")

        try:
            return await asyncio.to_thread(
                self._builder.update_definition,
                normalized_athlete_id,
                toml_content=toml_content,
                version=version,
                updated_by=updated_by,
            )
        except AgentDefinitionConflictError as exc:
            raise ConflictError(str(exc)) from exc
        except AgentDefinitionValidationError as exc:
            raise ValidationError(str(exc)) from exc
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    async def delete_definition(self, athlete_id: str | int) -> dict[str, Any]:
        normalized_athlete_id = self._normalize_athlete_id(athlete_id)
        try:
            return await asyncio.to_thread(self._builder.delete_definition, normalized_athlete_id)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    async def validate_definition(self, toml_content: str) -> dict[str, Any]:
        if not isinstance(toml_content, str) or not toml_content.strip():
            raise ValidationError("Field 'toml_content' must be a non-empty string.")

        errors = await asyncio.to_thread(self._builder.validate_toml, toml_content)
        if errors:
            return {"valid": False, "errors": errors}
        return {"valid": True}
