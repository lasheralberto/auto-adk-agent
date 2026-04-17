from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib

try:
    import firebase_admin
    from firebase_admin import firestore as firebase_firestore
except Exception:  # noqa: BLE001
    firebase_admin = None
    firebase_firestore = None

from google.adk.agents import LlmAgent
from google.adk.skills import load_skill_from_dir
from google.adk.tools.agent_tool import AgentTool

try:
    from google.adk.planners import PlanReActPlanner
except Exception:  # noqa: BLE001
    PlanReActPlanner = None

from agent.agents.agent_prompts import WIKI_RESEARCH_CHAT_AGENT_ID
from agent.config.config import get_llm_provider
from agent.tools.pipeline import run_ingestion_pipeline, run_query_pipeline

logger = logging.getLogger(__name__)

MAX_DEFINITION_AGENTS = 10
DEFAULT_COLLECTION = "agent_definition_file"
SKILLS_DIR = pathlib.Path(__file__).parent / "skills"
VALID_PLANNER_MODES = {"always", "full_only", "off"}
AGENT_ID_RE = re.compile(r"^[a-z0-9_]+$")

SYSTEM_ALLOWED_FIELDS = {"entrypoint", "model", "planner_mode"}
PROMPT_AGENT_ALLOWED_FIELDS = {"id", "prompt", "order"}
ROOT_ALLOWED_FIELDS = {"system", "prompt_agents"}

RESERVED_AGENT_IDS = {
    "intent_router",
    "plan_react_planner",
    "strava_ingestion_agent",
    "query_agent",
    "answer_agent",
    "orchestrator",
    WIKI_RESEARCH_CHAT_AGENT_ID,
}

DEFAULT_PROMPT_TEXT = (
    "Analiza la consulta del atleta y devuelve una respuesta accionable en espanol. "
    "Incluye recomendaciones practicas y explica brevemente el por que."
)

TOOL_CATALOG: dict[str, Callable[..., Any]] = {
    "run_ingestion_pipeline": run_ingestion_pipeline,
    "run_query_pipeline": run_query_pipeline,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_planner_mode(mode: str | None, fallback: str = "full_only") -> str:
    normalized = (mode or fallback).strip().lower()
    if normalized in VALID_PLANNER_MODES:
        return normalized
    return fallback


def _build_runtime_planner_directive(normalized_planner_mode: str) -> str:
    if normalized_planner_mode == "always":
        return "Always run plan_react_planner before delegation."
    if normalized_planner_mode == "off":
        return "Do not run plan_react_planner."
    return "Run plan_react_planner only when intent_router outputs FULL_EXECUTION."


def _build_default_orchestrator_instruction(
    planner_mode: str,
    *,
    custom_prompt_agent_ids: list[str] | None = None,
) -> str:
    planner_directive = _build_runtime_planner_directive(planner_mode)
    custom_prompt_agent_ids = custom_prompt_agent_ids or []

    custom_lines = ""
    if custom_prompt_agent_ids:
        items = "\n".join(f"- {agent_id}" for agent_id in custom_prompt_agent_ids)
        custom_lines = (
            "\nCustom prompt agents (user-defined):\n"
            f"{items}\n"
            "When a custom prompt agent is relevant, delegate to it before finalizing.\n"
        )

    return (
        "You are the orchestrator for a Strava training assistant.\n\n"
        "Internal agents:\n"
        "- intent_router\n"
        "- plan_react_planner\n"
        "- strava_ingestion_agent\n"
        "- query_agent\n"
        "- answer_agent\n"
        f"{custom_lines}\n"
        "Routing rules:\n"
        "- For user questions about training data, delegate to query_agent first.\n"
        "- For sync/ingestion requests, use strava_ingestion_agent.\n"
        "- Use answer_agent only for generic conversation or final wording.\n"
        "- Pipeline stages are handled externally via API endpoints.\n\n"
        f"Runtime directive: {planner_directive}"
    )


def _build_default_toml_template() -> str:
    return _stringify_prompt_only_definition(
        {
            "system": {
                "entrypoint": "orchestrator",
                "model": "",
                "planner_mode": "full_only",
            },
            "prompt_agents": [
                {
                    "id": "agent_1",
                    "prompt": DEFAULT_PROMPT_TEXT,
                    "order": 1,
                }
            ],
        }
    )


def _parse_toml(toml_content: str) -> dict[str, Any]:
    try:
        payload = tomllib.loads(toml_content)
    except Exception as exc:  # noqa: BLE001
        raise AgentDefinitionValidationError(f"Invalid TOML syntax: {exc}") from exc

    if not isinstance(payload, dict):
        raise AgentDefinitionValidationError("Invalid TOML payload: expected table at root.")

    return payload


def _normalize_prompt_agent_id(value: str, *, index: int) -> str:
    normalized = value.strip().lower()
    normalized = re.sub(r"\s+", "_", normalized)
    normalized = re.sub(r"[^a-z0-9_]", "", normalized)
    if not normalized:
        normalized = f"agent_{index}"
    return normalized


def _extract_prompt_agents_from_v2(raw_prompt_agents: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_prompt_agents, list):
        return []

    prompt_agents: list[dict[str, Any]] = []
    for index, raw_agent in enumerate(raw_prompt_agents, start=1):
        if not isinstance(raw_agent, dict):
            continue

        raw_id = str(raw_agent.get("id") or "")
        prompt_id = _normalize_prompt_agent_id(raw_id, index=index)
        prompt = str(raw_agent.get("prompt") or "")

        raw_order = raw_agent.get("order")
        if isinstance(raw_order, int):
            order = raw_order
        else:
            order = index

        prompt_agents.append(
            {
                "id": prompt_id,
                "prompt": prompt,
                "order": order,
            }
        )

    return prompt_agents


def _extract_prompt_agents_from_legacy(raw_agents: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_agents, list):
        return []

    prompt_agents: list[dict[str, Any]] = []
    for index, raw_agent in enumerate(raw_agents, start=1):
        if not isinstance(raw_agent, dict):
            continue

        raw_id = str(raw_agent.get("id") or "").strip()
        normalized_id = _normalize_prompt_agent_id(raw_id, index=index)
        if normalized_id in RESERVED_AGENT_IDS:
            continue

        instruction = raw_agent.get("instruction")
        if not isinstance(instruction, str) or not instruction.strip():
            continue

        prompt_agents.append(
            {
                "id": normalized_id,
                "prompt": instruction,
                "order": len(prompt_agents) + 1,
            }
        )

    return prompt_agents


def _to_prompt_only_definition(parsed: dict[str, Any]) -> dict[str, Any]:
    root = parsed if isinstance(parsed, dict) else {}
    raw_system = root.get("system") if isinstance(root.get("system"), dict) else {}

    system = {
        "entrypoint": str(raw_system.get("entrypoint") or "orchestrator").strip() or "orchestrator",
        "model": str(raw_system.get("model") or ""),
        "planner_mode": _normalize_planner_mode(
            str(raw_system.get("planner_mode") or "full_only"),
            fallback="full_only",
        ),
    }

    prompt_agents = _extract_prompt_agents_from_v2(root.get("prompt_agents"))
    if not prompt_agents:
        prompt_agents = _extract_prompt_agents_from_legacy(root.get("agents"))

    return {
        "system": system,
        "prompt_agents": prompt_agents,
    }


def _validate_definition(parsed: dict[str, Any], *, strict: bool) -> list[str]:
    errors: list[str] = []
    root = parsed if isinstance(parsed, dict) else {}
    normalized = _to_prompt_only_definition(root)

    raw_system = root.get("system")
    if not isinstance(raw_system, dict):
        errors.append("Missing [system] table.")
        raw_system = {}

    raw_entrypoint = raw_system.get("entrypoint")
    if not isinstance(raw_entrypoint, str) or not raw_entrypoint.strip():
        errors.append("Field 'system.entrypoint' is required.")

    if normalized["system"]["entrypoint"] != "orchestrator":
        errors.append("Entrypoint must be 'orchestrator' in v2.")

    raw_system_planner_mode = raw_system.get("planner_mode")
    if raw_system_planner_mode is not None:
        if not isinstance(raw_system_planner_mode, str):
            errors.append("Field 'system.planner_mode' must be a string.")
        elif _normalize_planner_mode(raw_system_planner_mode, "") != raw_system_planner_mode.strip().lower():
            errors.append("Field 'system.planner_mode' must be one of: always, full_only, off.")

    prompt_agents = normalized["prompt_agents"]
    if not prompt_agents:
        errors.append("At least one [[prompt_agents]] entry is required.")

    if len(prompt_agents) > MAX_DEFINITION_AGENTS:
        errors.append(f"Definition exceeds max agents: {len(prompt_agents)} > {MAX_DEFINITION_AGENTS}.")

    seen_ids: set[str] = set()
    for prompt_agent in prompt_agents:
        prompt_id = str(prompt_agent.get("id") or "").strip()
        prompt = str(prompt_agent.get("prompt") or "")

        if not prompt_id:
            errors.append("Prompt agent entry missing required 'id'.")
            continue

        if not AGENT_ID_RE.match(prompt_id):
            errors.append(f"Prompt agent '{prompt_id}' must be snake_case.")

        if prompt_id in RESERVED_AGENT_IDS:
            errors.append(f"Prompt agent id '{prompt_id}' is reserved.")

        if prompt_id in seen_ids:
            errors.append(f"Duplicate prompt agent id '{prompt_id}'.")
        seen_ids.add(prompt_id)

        if not prompt.strip():
            errors.append(f"Prompt agent '{prompt_id}' must define non-empty 'prompt'.")

    if strict:
        for key in root:
            if key not in ROOT_ALLOWED_FIELDS:
                errors.append(f"Field '{key}' is not allowed in prompt-only schema.")

        if isinstance(raw_system, dict):
            for key in raw_system:
                if key not in SYSTEM_ALLOWED_FIELDS:
                    errors.append(f"Field 'system.{key}' is not allowed in prompt-only schema.")

        raw_prompt_agents = root.get("prompt_agents")
        if isinstance(raw_prompt_agents, list):
            for index, raw_prompt_agent in enumerate(raw_prompt_agents, start=1):
                if not isinstance(raw_prompt_agent, dict):
                    errors.append(f"Entry [[prompt_agents]] #{index} must be a table.")
                    continue

                for key in raw_prompt_agent:
                    if key not in PROMPT_AGENT_ALLOWED_FIELDS:
                        errors.append(
                            f"Field 'prompt_agents[{index}].{key}' is not allowed in prompt-only schema."
                        )

                raw_order = raw_prompt_agent.get("order")
                if raw_order is not None and not isinstance(raw_order, int):
                    errors.append(f"Field 'prompt_agents[{index}].order' must be an integer.")

    return errors


def _extract_denormalized_fields(parsed: dict[str, Any]) -> dict[str, Any]:
    system = parsed.get("system") if isinstance(parsed.get("system"), dict) else {}
    prompt_agents = parsed.get("prompt_agents") if isinstance(parsed.get("prompt_agents"), list) else []

    prompt_agent_ids: list[str] = []
    for raw_prompt_agent in prompt_agents:
        if not isinstance(raw_prompt_agent, dict):
            continue
        prompt_id = raw_prompt_agent.get("id")
        if isinstance(prompt_id, str) and prompt_id.strip():
            prompt_agent_ids.append(prompt_id.strip())

    return {
        "entrypoint": str(system.get("entrypoint") or "").strip(),
        "prompt_agent_count": len(prompt_agent_ids),
        "prompt_agent_ids": prompt_agent_ids,
        "agent_count": len(prompt_agent_ids),
        "agent_ids": prompt_agent_ids,
    }


def _stringify_prompt_only_definition(parsed: dict[str, Any]) -> str:
    normalized = _to_prompt_only_definition(parsed)
    system = normalized["system"]

    prompt_agents = sorted(
        normalized["prompt_agents"],
        key=lambda item: (int(item.get("order") or 0), str(item.get("id") or "")),
    )

    lines = [
        "[system]",
        f"entrypoint = {json.dumps(str(system.get('entrypoint') or 'orchestrator'), ensure_ascii=False)}",
        f"model = {json.dumps(str(system.get('model') or ''), ensure_ascii=False)}",
        f"planner_mode = {json.dumps(_normalize_planner_mode(str(system.get('planner_mode') or 'full_only')), ensure_ascii=False)}",
    ]

    for prompt_agent in prompt_agents:
        prompt_id = str(prompt_agent.get("id") or "")
        prompt = str(prompt_agent.get("prompt") or "")
        order = int(prompt_agent.get("order") or 0)
        lines.extend(
            [
                "",
                "[[prompt_agents]]",
                f"id = {json.dumps(prompt_id, ensure_ascii=False)}",
                f"prompt = {json.dumps(prompt, ensure_ascii=False)}",
                f"order = {order}",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def _build_plan_react_kwargs(enabled: bool) -> dict[str, object]:
    if not enabled or PlanReActPlanner is None:
        return {}

    try:
        return {"planner": PlanReActPlanner()}
    except Exception:  # noqa: BLE001
        return {}


def _load_skill_instruction(skill_name: str, *, fallback: str) -> str:
    try:
        skill = load_skill_from_dir(SKILLS_DIR / skill_name)
        instructions = str(getattr(skill, "instructions", "") or "").strip()
        if instructions:
            return instructions
    except Exception:  # noqa: BLE001
        logger.warning("Failed to load skill '%s'. Using fallback instruction.", skill_name)

    return fallback


DEFAULT_TOML_TEMPLATE = _build_default_toml_template()


class AgentDefinitionValidationError(ValueError):
    """Raised when a TOML definition is invalid."""


class AgentDefinitionConflictError(ValueError):
    """Raised when optimistic lock version does not match."""


@dataclass(slots=True)
class AgentDefinitionEntry:
    athlete_id: str
    toml_content: str
    version: int
    updated_at: str | None
    updated_by: str | None
    is_default: bool


@dataclass(slots=True)
class PromptAgentConfig:
    prompt_id: str
    prompt: str
    order: int


class AgentDefinitionStore:
    """Persistence for prompt-only agent TOML definitions."""

    def __init__(self) -> None:
        self._collection = os.environ.get("FIRESTORE_AGENT_DEFINITION_COLLECTION", DEFAULT_COLLECTION)
        self._state_path = Path(tempfile.gettempdir()) / "strava_agent_state" / "agent_definition_file.json"
        self._state_path.parent.mkdir(parents=True, exist_ok=True)

        self._client: Any | None = None
        use_firestore = os.environ.get("USE_FIRESTORE_STATE", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

        if use_firestore and firebase_firestore is not None and firebase_admin is not None:
            try:
                if not firebase_admin._apps:
                    project_id = os.environ.get("PROJECT_ID") or None
                    firebase_admin.initialize_app(options={"projectId": project_id} if project_id else None)
                self._client = firebase_firestore.client()
            except Exception:  # noqa: BLE001
                self._client = None

    @property
    def mode(self) -> str:
        return "firestore" if self._client is not None else "local"

    def _load_local(self) -> dict[str, Any]:
        if not self._state_path.exists():
            return {}
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_local(self, payload: dict[str, Any]) -> None:
        self._state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def default_entry(self, athlete_id: str) -> AgentDefinitionEntry:
        return AgentDefinitionEntry(
            athlete_id=athlete_id,
            toml_content=DEFAULT_TOML_TEMPLATE,
            version=0,
            updated_at=None,
            updated_by=None,
            is_default=True,
        )

    def get(self, athlete_id: str) -> AgentDefinitionEntry:
        normalized_athlete_id = (athlete_id or "").strip()
        if not normalized_athlete_id:
            raise ValueError("athlete_id is required.")

        if self._client is not None:
            try:
                doc = self._client.collection(self._collection).document(normalized_athlete_id).get()
                if doc.exists:
                    payload = doc.to_dict() or {}
                    return AgentDefinitionEntry(
                        athlete_id=normalized_athlete_id,
                        toml_content=str(payload.get("toml_content") or DEFAULT_TOML_TEMPLATE),
                        version=int(payload.get("version") or 0),
                        updated_at=payload.get("updated_at"),
                        updated_by=payload.get("updated_by"),
                        is_default=False,
                    )
            except Exception:  # noqa: BLE001
                logger.warning("Failed to read agent definition from Firestore. Falling back to local state.")
                self._client = None

        local = self._load_local()
        payload = local.get(normalized_athlete_id)
        if isinstance(payload, dict) and payload.get("toml_content"):
            return AgentDefinitionEntry(
                athlete_id=normalized_athlete_id,
                toml_content=str(payload.get("toml_content") or DEFAULT_TOML_TEMPLATE),
                version=int(payload.get("version") or 0),
                updated_at=payload.get("updated_at"),
                updated_by=payload.get("updated_by"),
                is_default=False,
            )

        return self.default_entry(normalized_athlete_id)

    def save(
        self,
        athlete_id: str,
        *,
        toml_content: str,
        expected_version: int,
        parsed: dict[str, Any],
        updated_by: str | None,
    ) -> AgentDefinitionEntry:
        normalized_athlete_id = (athlete_id or "").strip()
        if not normalized_athlete_id:
            raise ValueError("athlete_id is required.")

        denormalized = _extract_denormalized_fields(parsed)
        now_iso = _utc_now_iso()

        if self._client is not None:
            try:
                doc_ref = self._client.collection(self._collection).document(normalized_athlete_id)
                doc = doc_ref.get()
                current_version = int((doc.to_dict() or {}).get("version") or 0) if doc.exists else 0
                if current_version != expected_version:
                    raise AgentDefinitionConflictError(
                        f"Version conflict: expected {current_version}, got {expected_version}"
                    )

                next_version = current_version + 1
                payload = {
                    "athlete_id": normalized_athlete_id,
                    "toml_content": toml_content,
                    "version": next_version,
                    "updated_at": now_iso,
                    "updated_by": updated_by,
                    "entrypoint": denormalized["entrypoint"],
                    "prompt_agent_count": denormalized["prompt_agent_count"],
                    "prompt_agent_ids": denormalized["prompt_agent_ids"],
                    "agent_count": denormalized["agent_count"],
                    "agent_ids": denormalized["agent_ids"],
                }
                doc_ref.set(payload)

                return AgentDefinitionEntry(
                    athlete_id=normalized_athlete_id,
                    toml_content=toml_content,
                    version=next_version,
                    updated_at=now_iso,
                    updated_by=updated_by,
                    is_default=False,
                )
            except AgentDefinitionConflictError:
                raise
            except Exception:  # noqa: BLE001
                logger.warning("Failed to save agent definition in Firestore. Falling back to local state.")
                self._client = None

        local = self._load_local()
        current_payload = local.get(normalized_athlete_id)
        current_version = int(current_payload.get("version") or 0) if isinstance(current_payload, dict) else 0

        if current_version != expected_version:
            raise AgentDefinitionConflictError(
                f"Version conflict: expected {current_version}, got {expected_version}"
            )

        next_version = current_version + 1
        local[normalized_athlete_id] = {
            "athlete_id": normalized_athlete_id,
            "toml_content": toml_content,
            "version": next_version,
            "updated_at": now_iso,
            "updated_by": updated_by,
            "entrypoint": denormalized["entrypoint"],
            "prompt_agent_count": denormalized["prompt_agent_count"],
            "prompt_agent_ids": denormalized["prompt_agent_ids"],
            "agent_count": denormalized["agent_count"],
            "agent_ids": denormalized["agent_ids"],
        }
        self._save_local(local)

        return AgentDefinitionEntry(
            athlete_id=normalized_athlete_id,
            toml_content=toml_content,
            version=next_version,
            updated_at=now_iso,
            updated_by=updated_by,
            is_default=False,
        )

    def delete(self, athlete_id: str) -> bool:
        normalized_athlete_id = (athlete_id or "").strip()
        if not normalized_athlete_id:
            raise ValueError("athlete_id is required.")

        if self._client is not None:
            try:
                self._client.collection(self._collection).document(normalized_athlete_id).delete()
                return True
            except Exception:  # noqa: BLE001
                logger.warning("Failed to delete agent definition in Firestore. Falling back to local state.")
                self._client = None

        local = self._load_local()
        local.pop(normalized_athlete_id, None)
        self._save_local(local)
        return True


def _prompt_agent_configs(parsed: dict[str, Any]) -> list[PromptAgentConfig]:
    normalized = _to_prompt_only_definition(parsed)
    prompt_agents = sorted(
        normalized["prompt_agents"],
        key=lambda item: (int(item.get("order") or 0), str(item.get("id") or "")),
    )

    configs: list[PromptAgentConfig] = []
    for prompt_agent in prompt_agents:
        prompt_id = str(prompt_agent.get("id") or "").strip()
        prompt = str(prompt_agent.get("prompt") or "")
        order = int(prompt_agent.get("order") or 0)
        if not prompt_id or not prompt.strip():
            continue
        configs.append(PromptAgentConfig(prompt_id=prompt_id, prompt=prompt, order=order))

    return configs


class AgentDefinitionBuilder:
    def __init__(self, *, store: AgentDefinitionStore | None = None) -> None:
        self._store = store or AgentDefinitionStore()

    @property
    def store(self) -> AgentDefinitionStore:
        return self._store

    def validate_toml(self, toml_content: str) -> list[str]:
        if not isinstance(toml_content, str) or not toml_content.strip():
            return ["Field 'toml_content' must be a non-empty string."]

        try:
            parsed = _parse_toml(toml_content)
        except AgentDefinitionValidationError as exc:
            return [str(exc)]

        return _validate_definition(parsed, strict=True)

    def get_definition(self, athlete_id: str | int) -> dict[str, Any]:
        normalized_athlete_id = str(athlete_id).strip()
        if not normalized_athlete_id:
            raise ValueError("athlete_id is required.")

        entry = self._store.get(normalized_athlete_id)

        toml_content = entry.toml_content
        try:
            parsed = _parse_toml(entry.toml_content)
            normalized = _to_prompt_only_definition(parsed)
            errors = _validate_definition(normalized, strict=False)
            if errors:
                raise AgentDefinitionValidationError("; ".join(errors))
            toml_content = _stringify_prompt_only_definition(normalized)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Invalid definition for athlete %s when reading. Returning default template.",
                normalized_athlete_id,
            )
            toml_content = DEFAULT_TOML_TEMPLATE

        return {
            "athlete_id": normalized_athlete_id,
            "toml_content": toml_content,
            "version": entry.version,
            "is_default": entry.is_default,
            "updated_at": entry.updated_at,
        }

    def update_definition(
        self,
        athlete_id: str | int,
        *,
        toml_content: str,
        version: int,
        updated_by: str | None = None,
    ) -> dict[str, Any]:
        normalized_athlete_id = str(athlete_id).strip()
        if not normalized_athlete_id:
            raise ValueError("athlete_id is required.")

        if not isinstance(toml_content, str) or not toml_content.strip():
            raise AgentDefinitionValidationError("Field 'toml_content' must be a non-empty string.")

        parsed = _parse_toml(toml_content)
        errors = _validate_definition(parsed, strict=True)
        if errors:
            raise AgentDefinitionValidationError("; ".join(errors))

        normalized = _to_prompt_only_definition(parsed)
        normalized_toml = _stringify_prompt_only_definition(normalized)

        entry = self._store.save(
            normalized_athlete_id,
            toml_content=normalized_toml,
            expected_version=version,
            parsed=normalized,
            updated_by=updated_by,
        )

        return {
            "athlete_id": normalized_athlete_id,
            "toml_content": entry.toml_content,
            "version": entry.version,
            "is_default": False,
            "updated_at": entry.updated_at,
        }

    def delete_definition(self, athlete_id: str | int) -> dict[str, Any]:
        normalized_athlete_id = str(athlete_id).strip()
        if not normalized_athlete_id:
            raise ValueError("athlete_id is required.")

        self._store.delete(normalized_athlete_id)
        return {"deleted": True}

    def resolve_wiki_chat_instruction_from_custom_definition(
        self,
        athlete_id: str | int,
        *,
        agent_id: str | None = None,
    ) -> str | None:
        normalized_athlete_id = str(athlete_id).strip()
        if not normalized_athlete_id:
            return None

        entry = self._store.get(normalized_athlete_id)
        if entry.is_default:
            return None

        try:
            parsed = _parse_toml(entry.toml_content)
            normalized = _to_prompt_only_definition(parsed)
            errors = _validate_definition(normalized, strict=False)
            if errors:
                return None

            target_id = (agent_id or WIKI_RESEARCH_CHAT_AGENT_ID).strip() or WIKI_RESEARCH_CHAT_AGENT_ID
            for prompt_agent in normalized.get("prompt_agents", []):
                if not isinstance(prompt_agent, dict):
                    continue
                if str(prompt_agent.get("id") or "").strip() != target_id:
                    continue
                candidate = str(prompt_agent.get("prompt") or "").strip()
                if candidate:
                    return candidate
            return None
        except Exception:  # noqa: BLE001
            return None

    def build_orchestrator(
        self,
        *,
        athlete_id: str | int | None,
        model_name: str | None = None,
        planner_mode: str | None = None,
    ) -> LlmAgent:
        if athlete_id is None or str(athlete_id).strip() == "":
            entry = self._store.default_entry(athlete_id="default")
        else:
            entry = self._store.get(str(athlete_id).strip())

        try:
            parsed = _parse_toml(entry.toml_content)
            normalized = _to_prompt_only_definition(parsed)
            errors = _validate_definition(normalized, strict=False)
            if errors:
                raise AgentDefinitionValidationError("; ".join(errors))
        except Exception:
            if entry.is_default:
                normalized = _to_prompt_only_definition(_parse_toml(DEFAULT_TOML_TEMPLATE))
            else:
                logger.warning(
                    "Invalid custom agent definition for athlete %s. Falling back to default template.",
                    entry.athlete_id,
                )
                normalized = _to_prompt_only_definition(_parse_toml(DEFAULT_TOML_TEMPLATE))

        system = normalized.get("system") if isinstance(normalized.get("system"), dict) else {}
        system_model = str(system.get("model") or "").strip() or None
        system_planner_mode = _normalize_planner_mode(
            str(system.get("planner_mode") or "full_only"),
            fallback="full_only",
        )
        runtime_planner_mode = _normalize_planner_mode(
            planner_mode,
            fallback=system_planner_mode,
        )

        active_model = model_name.strip() if isinstance(model_name, str) and model_name.strip() else system_model
        selected_model = get_llm_provider(model_name=active_model)

        intent_router_instruction = _load_skill_instruction(
            "intent-router",
            fallback="Route each request between EARLY and FULL execution.",
        )
        plan_react_instruction = _load_skill_instruction(
            "plan-react-planner",
            fallback="Produce a structured planning and reasoning breakdown before execution.",
        )
        ingestion_instruction = _load_skill_instruction(
            "strava-ingestion-agent",
            fallback="Handle athlete data sync and ingestion tasks.",
        )
        query_instruction = _load_skill_instruction(
            "query-agent",
            fallback="Query indexed athlete context and return grounded findings.",
        )
        answer_instruction = _load_skill_instruction(
            "answer-agent",
            fallback="Return concise final answers in natural language.",
        )

        intent_router = LlmAgent(
            name="intent_router",
            model=selected_model,
            instruction=intent_router_instruction,
            tools=[],
        )

        planner_kwargs = _build_plan_react_kwargs(enabled=runtime_planner_mode != "off")
        try:
            plan_react_planner = LlmAgent(
                name="plan_react_planner",
                model=selected_model,
                instruction=plan_react_instruction,
                tools=[],
                **planner_kwargs,
            )
        except TypeError:
            plan_react_planner = LlmAgent(
                name="plan_react_planner",
                model=selected_model,
                instruction=plan_react_instruction,
                tools=[],
            )

        strava_ingestion_agent = LlmAgent(
            name="strava_ingestion_agent",
            model=selected_model,
            instruction=ingestion_instruction,
            tools=[TOOL_CATALOG["run_ingestion_pipeline"]],
        )

        query_agent = LlmAgent(
            name="query_agent",
            model=selected_model,
            instruction=query_instruction,
            tools=[TOOL_CATALOG["run_query_pipeline"]],
        )

        answer_agent = LlmAgent(
            name="answer_agent",
            model=selected_model,
            instruction=answer_instruction,
            tools=[],
        )

        prompt_configs = _prompt_agent_configs(normalized)
        prompt_tools: list[AgentTool] = []
        for config in prompt_configs:
            prompt_agent = LlmAgent(
                name=config.prompt_id,
                model=selected_model,
                instruction=config.prompt,
                tools=[],
            )
            prompt_tools.append(AgentTool(agent=prompt_agent))

        orchestrator_tools: list[Any] = [
            AgentTool(agent=intent_router),
        ]
        if runtime_planner_mode != "off":
            orchestrator_tools.append(AgentTool(agent=plan_react_planner))

        orchestrator_tools.extend(
            [
                AgentTool(agent=strava_ingestion_agent),
                AgentTool(agent=query_agent),
                AgentTool(agent=answer_agent),
                *prompt_tools,
            ]
        )

        orchestrator_instruction = _build_default_orchestrator_instruction(
            runtime_planner_mode,
            custom_prompt_agent_ids=[item.prompt_id for item in prompt_configs],
        )

        return LlmAgent(
            name="orchestrator",
            model=selected_model,
            instruction=orchestrator_instruction,
            tools=orchestrator_tools,
        )
