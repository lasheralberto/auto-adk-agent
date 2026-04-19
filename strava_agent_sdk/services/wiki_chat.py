from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from agent.builder import AgentDefinitionBuilder
from agent.agents.agent_prompts import (
    AgentPromptStore,
    WIKI_CONTEXT_BLOCK,
    WIKI_RESEARCH_CHAT_AGENT_ID,
    render_template,
)
from agent.agents.wiki_research_chat_agent import build_wiki_research_chat_agent, read_wiki_content
from agent.config.config import get_llm_provider
from agent.runner import run_agent, run_agent_streaming

from strava_agent_sdk.errors import NotFoundError, ValidationError
from strava_agent_sdk.types import ChatResponse


_definition_builder = AgentDefinitionBuilder()


def _normalize_chat_result(result: object) -> dict[str, Any]:
    if isinstance(result, dict):
        response_text = result.get("response")
        if response_text is None:
            response_text = result.get("message")
        if response_text is None:
            response_text = result.get("answer")

        tool_calls = result.get("tool_calls")
        if not isinstance(tool_calls, list):
            tool_calls = []

        normalized_result: dict[str, Any] = {
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


class WikiChatService:
    async def chat(
        self,
        *,
        question: str,
        athlete_id: int,
        model_name: str | None = None,
        agent_id: str | None = None,
    ) -> ChatResponse:
        prepared = await self._prepare_wiki_agent(
            question=question,
            athlete_id=athlete_id,
            model_name=model_name,
            agent_id=agent_id,
        )

        result = await run_agent(
            prepared["question"],
            prepared["wiki_agent"],
            athlete_id=athlete_id,
            conversation_question=prepared["question"],
        )
        normalized = _normalize_chat_result(result)
        normalized["athlete_id"] = athlete_id
        return ChatResponse.from_payload(normalized)

    async def chat_stream(
        self,
        *,
        question: str,
        athlete_id: int,
        model_name: str | None = None,
        agent_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        prepared = await self._prepare_wiki_agent(
            question=question,
            athlete_id=athlete_id,
            model_name=model_name,
            agent_id=agent_id,
        )

        async for chunk in run_agent_streaming(
            prepared["question"],
            prepared["wiki_agent"],
            athlete_id=athlete_id,
            conversation_question=prepared["question"],
        ):
            yield chunk

    async def _prepare_wiki_agent(
        self,
        *,
        question: str,
        athlete_id: int,
        model_name: str | None,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_question = (question or "").strip()
        if not normalized_question:
            raise ValidationError("Field 'message' or 'question' must be a non-empty string.")
        if athlete_id <= 0:
            raise ValidationError("Field 'athlete_id' is required.")

        resolved_agent_id = (agent_id or "").strip() or WIKI_RESEARCH_CHAT_AGENT_ID

        wiki_content = await asyncio.to_thread(read_wiki_content, athlete_id, normalized_question)
        if wiki_content is None:
            raise NotFoundError(
                f"wiki_not_found: No se encontro la wiki para el atleta {athlete_id}."
            )

        escaped_wiki = wiki_content.replace("{", "{{").replace("}", "}}")
        wiki_context_block = render_template(
            WIKI_CONTEXT_BLOCK,
            {
                "%%ATHLETE_ID%%": str(athlete_id),
                "%%WIKI%%": escaped_wiki,
            },
        )

        # Try multi-agent consensus pipeline first (2+ custom agents in TOML)
        consensus_agent = await asyncio.to_thread(
            _definition_builder.build_wiki_consensus_agent,
            athlete_id=athlete_id,
            wiki_context_block=wiki_context_block,
            model_name=model_name,
        )

        if consensus_agent is not None:
            return {
                "question": normalized_question,
                "wiki_agent": consensus_agent,
            }

        # Single-agent fallback: resolve one instruction template
        selected_model = get_llm_provider(model_name=model_name)

        instruction_template = await asyncio.to_thread(
            _definition_builder.resolve_wiki_chat_instruction_from_custom_definition,
            athlete_id,
            agent_id=resolved_agent_id,
        )

        if not instruction_template:
            try:
                instruction_template = await asyncio.to_thread(
                    AgentPromptStore().get_template,
                    resolved_agent_id,
                )
            except ValueError:
                instruction_template = await asyncio.to_thread(
                    AgentPromptStore().get_template,
                    WIKI_RESEARCH_CHAT_AGENT_ID,
                )

        wiki_agent = build_wiki_research_chat_agent(
            selected_model=selected_model,
            wiki_content=wiki_content,
            athlete_id=athlete_id,
            instruction_template=instruction_template,
        )

        return {
            "question": normalized_question,
            "wiki_agent": wiki_agent,
        }
