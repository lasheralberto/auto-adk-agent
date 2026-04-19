from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from agent.app import build_orchestrator
from agent.runner import get_conversation_chain_logs, run_agent, run_agent_streaming
from agent.tools.pipeline.storage_backend import AthleteStateStore
from agent.tools.pipeline.workflow import run_query_layer

from strava_agent_sdk.errors import ValidationError
from strava_agent_sdk.types import ChatResponse


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


def _normalize_response_format(response_format: str | None) -> str:
    normalized = (response_format or "plan_react_v1").strip().lower()
    if normalized in {"plain", "plan_react_v1", "structured"}:
        return normalized
    return "plan_react_v1"


def _normalize_planner_mode(planner_mode: str | None) -> str:
    normalized = (planner_mode or "full_only").strip().lower()
    if normalized in {"off", "always", "full_only"}:
        return normalized
    return "full_only"


class ChatService:
    async def get_agent_chain_logs(
        self,
        *,
        athlete_id: int,
        page: int = 1,
        page_size: int = 5,
        include_events: bool = False,
    ) -> dict[str, Any]:
        if athlete_id <= 0:
            raise ValidationError("Field 'athlete_id' is required.")

        resolved_page = max(1, int(page))
        resolved_page_size = max(1, min(int(page_size), 50))

        return await asyncio.to_thread(
            get_conversation_chain_logs,
            int(athlete_id),
            page=resolved_page,
            page_size=resolved_page_size,
            include_events=bool(include_events),
        )

    async def query(
        self,
        *,
        question: str,
        athlete_id: int,
        top_k: int = 5,
        target_date: str = "",
    ) -> dict[str, Any]:
        normalized_question = (question or "").strip()
        if not normalized_question:
            raise ValidationError("Field 'question' or 'message' is required.")
        if athlete_id <= 0:
            raise ValidationError("Field 'athlete_id' is required.")

        resolved_top_k = max(1, min(int(top_k), 20))
        normalized_target_date = (target_date or "").strip()

        return await asyncio.to_thread(
            run_query_layer,
            question=normalized_question,
            athlete_id=int(athlete_id),
            top_k=resolved_top_k,
            target_date=normalized_target_date,
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
        prepared = await self._prepare_chat(
            question=question,
            athlete_id=athlete_id,
            model_name=model_name,
            top_k=top_k,
            target_date=target_date,
            access_token=access_token,
            planner_mode=planner_mode,
        )

        normalized_response_format = _normalize_response_format(response_format)
        result = await run_agent(
            prepared["augmented_question"],
            prepared["orchestrator"],
            response_format=normalized_response_format,
            athlete_id=athlete_id,
            conversation_question=question,
        )
        normalized = _normalize_chat_result(result)
        normalized["retrieval_hits"] = prepared["retrieval_hits"]
        normalized["query_mode"] = prepared["query_mode"]
        return ChatResponse.from_payload(normalized)

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
        prepared = await self._prepare_chat(
            question=question,
            athlete_id=athlete_id,
            model_name=model_name,
            top_k=top_k,
            target_date=target_date,
            access_token=access_token,
            planner_mode=planner_mode,
        )

        normalized_response_format = _normalize_response_format(response_format)
        async for chunk in run_agent_streaming(
            prepared["augmented_question"],
            prepared["orchestrator"],
            response_format=normalized_response_format,
            athlete_id=athlete_id,
            conversation_question=question,
        ):
            yield chunk

    async def _prepare_chat(
        self,
        *,
        question: str,
        athlete_id: int,
        model_name: str | None,
        top_k: int,
        target_date: str,
        access_token: str | None,
        planner_mode: str | None,
    ) -> dict[str, Any]:
        normalized_question = (question or "").strip()
        if not normalized_question:
            raise ValidationError("Field 'message' or 'question' must be a non-empty string.")
        if athlete_id <= 0:
            raise ValidationError("Field 'athlete_id' is required for multi-athlete retrieval.")

        retrieval_payload = await self.query(
            question=normalized_question,
            athlete_id=athlete_id,
            top_k=top_k,
            target_date=target_date,
        )
        retrieval_context = str(retrieval_payload.get("context") or "").strip()
        retrieval_hits = retrieval_payload.get("hits")
        if not isinstance(retrieval_hits, list):
            retrieval_hits = []

        augmented_question = normalized_question

        normalized_access_token = (access_token or "").strip()
        if normalized_access_token:
            await asyncio.to_thread(
                AthleteStateStore().upsert_tokens,
                int(athlete_id),
                {
                    "access_token": normalized_access_token,
                    "athlete": {"id": int(athlete_id)},
                },
            )
            auth_context = (
                "Contexto de autenticacion Strava para esta sesion:\n"
                f"- athlete_id autenticado: {athlete_id}\n"
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
                f"Pregunta del usuario: {normalized_question}\n"
                "Responde con base en el contexto recuperado. "
                "Si no hay evidencia suficiente, dilo y sugiere correr pipeline diario."
            )
        else:
            augmented_question = (
                f"Pregunta del usuario: {normalized_question}\n"
                "No se encontro contexto en el indice para este atleta. "
                "Responde con esta limitacion y sugiere ejecutar pipeline diario."
            )

        augmented_question = (
            f"Contexto de atleta para herramientas: athlete_id={athlete_id}.\n"
            f"{augmented_question}"
        )

        orchestrator = build_orchestrator(
            model_name=model_name,
            planner_mode=_normalize_planner_mode(planner_mode),
            athlete_id=athlete_id,
        )

        return {
            "orchestrator": orchestrator,
            "augmented_question": augmented_question,
            "retrieval_hits": retrieval_hits,
            "query_mode": retrieval_payload.get("mode"),
        }
