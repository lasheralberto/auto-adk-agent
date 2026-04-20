from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
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

from google.adk.agents import LlmAgent, SequentialAgent
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
VALID_AGENT_TYPES = {"llm", "sequential", "parallel", "loop", "custom"}
AGENT_ID_RE = re.compile(r"^[a-z0-9_]+$")
CONSENSUS_ITERATIONS = 2
CONSENSUS_TOOL_ID = "consensus_loop_pipeline"

AGENT_TYPE_ALIASES = {
    "llm": "llm",
    "llmagent": "llm",
    "sequential": "sequential",
    "sequentialagent": "sequential",
    "parallel": "parallel",
    "parallelagent": "parallel",
    "loop": "loop",
    "loopagent": "loop",
    "custom": "custom",
    "customagent": "custom",
}

SYSTEM_ALLOWED_FIELDS = {"entrypoint"}
AGENT_ALLOWED_FIELDS = {
    "id",
    "name",
    "type",
    "model",
    "prompt",
    "instruction",
    "instructions",
    "description",
    "sub_agents",
    "output_key",
    "custom_type",
    "custom_class",
    "custom_factory",
    "order",
}
ROOT_ALLOWED_FIELDS = {"system", "agents", "workflow"}

# Legacy v2 support
LEGACY_SYSTEM_ALLOWED_FIELDS = {"entrypoint", "model", "planner_mode"}
LEGACY_PROMPT_AGENT_ALLOWED_FIELDS = {"id", "prompt", "order"}
LEGACY_ROOT_ALLOWED_FIELDS = {"system", "prompt_agents"}

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

CUSTOM_AGENT_REGISTRY: dict[str, Callable[[Any, list[Any]], Any]] = {}


def _normalize_custom_agent_key(value: str) -> str:
    normalized = str(value or "").strip().lower()
    normalized = re.sub(r"\s+", "_", normalized)
    normalized = re.sub(r"[^a-z0-9_]", "", normalized)
    return normalized


def register_custom_agent(custom_type: str, factory: Callable[[Any, list[Any]], Any]) -> None:
    """Register a backend factory for TOML agents with type=custom."""
    key = _normalize_custom_agent_key(custom_type)
    if not key:
        raise ValueError("custom_type must be a non-empty string.")
    if not callable(factory):
        raise ValueError("factory must be callable.")
    CUSTOM_AGENT_REGISTRY[key] = factory


def unregister_custom_agent(custom_type: str) -> bool:
    key = _normalize_custom_agent_key(custom_type)
    if not key:
        return False
    return CUSTOM_AGENT_REGISTRY.pop(key, None) is not None


def list_registered_custom_agents() -> list[str]:
    return sorted(CUSTOM_AGENT_REGISTRY.keys())


def _resolve_custom_agent_factory(custom_ref: str) -> Callable[[Any, list[Any]], Any] | None:
    key = _normalize_custom_agent_key(custom_ref)
    if not key:
        return None
    return CUSTOM_AGENT_REGISTRY.get(key)


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
    custom_agent_ids: list[str] | None = None,
    consensus_tool_id: str | None = None,
    consensus_rounds: int = CONSENSUS_ITERATIONS,
) -> str:
    planner_directive = _build_runtime_planner_directive(planner_mode)
    custom_agent_ids = custom_agent_ids or []

    custom_lines = ""
    if consensus_tool_id and custom_agent_ids:
        items = "\n".join(f"- {agent_id}" for agent_id in custom_agent_ids)
        custom_lines = (
            "\nConsensus loop (user-defined):\n"
            f"- tool: {consensus_tool_id}\n"
            f"- rounds: {consensus_rounds}\n"
            "Participants:\n"
            f"{items}\n"
            "For athlete questions, run this tool once before finalizing.\n"
        )
    elif custom_agent_ids:
        items = "\n".join(f"- {agent_id}" for agent_id in custom_agent_ids)
        custom_lines = (
            "\nCustom agents (user-defined):\n"
            f"{items}\n"
            "When a custom agent is relevant, delegate to it before finalizing.\n"
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
        "- If consensus loop is available and user asks an athlete question, run it first.\n"
        "- If extra retrieval is needed after consensus, delegate to query_agent.\n"
        "- For sync/ingestion requests, use strava_ingestion_agent.\n"
        "- Use answer_agent only for generic conversation or final wording.\n"
        "- Pipeline stages are handled externally via API endpoints.\n\n"
        f"Runtime directive: {planner_directive}"
    )


def _build_default_toml_template() -> str:
    return _stringify_v3_definition(
        {
            "system": {"entrypoint": "orchestrator"},
            "agents": [
                {
                    "id": "agent_1",
                    "type": "llm",
                    "model": "",
                    "description": "",
                    "prompt": DEFAULT_PROMPT_TEXT,
                    "sub_agents": [],
                    "output_key": "",
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


def _normalize_agent_id(value: str, *, index: int) -> str:
    normalized = value.strip().lower()
    normalized = re.sub(r"\s+", "_", normalized)
    normalized = re.sub(r"[^a-z0-9_]", "", normalized)
    if not normalized:
        normalized = f"agent_{index}"
    return normalized


def _normalize_agent_type(value: Any, *, fallback: str = "llm") -> str:
    normalized = str(value or fallback).strip().lower()
    return AGENT_TYPE_ALIASES.get(normalized, normalized)


def _extract_prompt(raw_agent: dict[str, Any]) -> str:
    """Read prompt from v3-compatible fields, including model aliases."""
    prompt = raw_agent.get("prompt")
    if isinstance(prompt, str):
        return prompt

    instruction = raw_agent.get("instruction")
    if isinstance(instruction, str):
        return instruction

    instructions = raw_agent.get("instructions")
    if isinstance(instructions, str):
        return instructions

    return ""


# ---------------------------------------------------------------------------
# v3 normalization
# ---------------------------------------------------------------------------

def _extract_agents_v3(raw_agents: Any) -> list[dict[str, Any]]:
    """Extract agent entries from v3 [[agents]] format."""
    if not isinstance(raw_agents, list):
        return []

    agents: list[dict[str, Any]] = []
    for index, raw_agent in enumerate(raw_agents, start=1):
        if not isinstance(raw_agent, dict):
            continue

        raw_id = str(raw_agent.get("id") or "")
        agent_id = _normalize_agent_id(raw_id, index=index)
        agent_type = _normalize_agent_type(raw_agent.get("type"), fallback="llm")
        if agent_type not in VALID_AGENT_TYPES:
            agent_type = "llm"

        name = str(raw_agent.get("name") or "")
        model = str(raw_agent.get("model") or "")
        description = str(raw_agent.get("description") or "")
        prompt = _extract_prompt(raw_agent)
        output_key = str(raw_agent.get("output_key") or "")
        custom_type = str(
            raw_agent.get("custom_type")
            or raw_agent.get("custom_class")
            or raw_agent.get("custom_factory")
            or ""
        )

        raw_sub_agents = raw_agent.get("sub_agents")
        sub_agents: list[str] = []
        if isinstance(raw_sub_agents, list):
            for ref in raw_sub_agents:
                ref_str = str(ref).strip()
                if ref_str:
                    sub_agents.append(ref_str)

        raw_order = raw_agent.get("order")
        order = raw_order if isinstance(raw_order, int) else index

        agents.append({
            "id": agent_id,
            "name": name,
            "type": agent_type,
            "model": model,
            "description": description,
            "prompt": prompt,
            "sub_agents": sub_agents,
            "output_key": output_key,
            "custom_type": custom_type,
            "order": order,
        })

    return agents


def _extract_agents_from_named_tables(
    raw_tables: Any,
    *,
    default_type: str,
    order_offset: int = 0,
) -> list[dict[str, Any]]:
    """Extract agents from TOML named tables: [agents.id] / [workflow.id]."""
    if not isinstance(raw_tables, dict):
        return []

    agents: list[dict[str, Any]] = []
    for index, (table_id, raw_agent) in enumerate(raw_tables.items(), start=1):
        if not isinstance(raw_agent, dict):
            continue

        raw_id = str(raw_agent.get("id") or table_id or "")
        agent_id = _normalize_agent_id(raw_id, index=order_offset + index)

        agent_type = _normalize_agent_type(raw_agent.get("type"), fallback=default_type)
        if agent_type not in VALID_AGENT_TYPES:
            # Keep unknown values for explicit validation errors later.
            agent_type = str(raw_agent.get("type") or default_type).strip().lower()

        name = str(raw_agent.get("name") or table_id or agent_id)
        model = str(raw_agent.get("model") or "")
        description = str(raw_agent.get("description") or "")
        prompt = _extract_prompt(raw_agent)
        output_key = str(raw_agent.get("output_key") or "")
        custom_type = str(
            raw_agent.get("custom_type")
            or raw_agent.get("custom_class")
            or raw_agent.get("custom_factory")
            or ""
        )

        raw_sub_agents = raw_agent.get("sub_agents")
        sub_agents: list[str] = []
        if isinstance(raw_sub_agents, list):
            for ref in raw_sub_agents:
                ref_str = str(ref).strip()
                if ref_str:
                    sub_agents.append(ref_str)

        raw_order = raw_agent.get("order")
        order = raw_order if isinstance(raw_order, int) else (order_offset + index)

        agents.append({
            "id": agent_id,
            "name": name,
            "type": agent_type,
            "model": model,
            "description": description,
            "prompt": prompt,
            "sub_agents": sub_agents,
            "output_key": output_key,
            "custom_type": custom_type,
            "order": order,
        })

    return agents


def _migrate_v2_to_v3(parsed: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2 prompt-only format to v3."""
    root = parsed if isinstance(parsed, dict) else {}
    raw_system = root.get("system") if isinstance(root.get("system"), dict) else {}

    system = {"entrypoint": str(raw_system.get("entrypoint") or "orchestrator").strip() or "orchestrator"}

    agents: list[dict[str, Any]] = []

    # Try v2 prompt_agents
    raw_prompt_agents = root.get("prompt_agents")
    if isinstance(raw_prompt_agents, list):
        for index, raw_agent in enumerate(raw_prompt_agents, start=1):
            if not isinstance(raw_agent, dict):
                continue
            raw_id = str(raw_agent.get("id") or "")
            agent_id = _normalize_agent_id(raw_id, index=index)
            prompt = str(raw_agent.get("prompt") or "")
            raw_order = raw_agent.get("order")
            order = raw_order if isinstance(raw_order, int) else index

            agents.append({
                "id": agent_id,
                "name": str(raw_agent.get("name") or agent_id),
                "type": "llm",
                "model": str(raw_agent.get("model") or ""),
                "description": "",
                "prompt": prompt,
                "sub_agents": [],
                "output_key": "",
                "custom_type": "",
                "order": order,
            })

    # Try legacy [[agents]] with instruction
    if not agents:
        raw_legacy = root.get("agents")
        if isinstance(raw_legacy, list):
            for index, raw_agent in enumerate(raw_legacy, start=1):
                if not isinstance(raw_agent, dict):
                    continue
                raw_id = str(raw_agent.get("id") or "").strip()
                normalized_id = _normalize_agent_id(raw_id, index=index)
                if normalized_id in RESERVED_AGENT_IDS:
                    continue
                prompt = _extract_prompt(raw_agent)
                if not prompt.strip():
                    continue
                agents.append({
                    "id": normalized_id,
                    "name": raw_id or normalized_id,
                    "type": "llm",
                    "model": "",
                    "description": "",
                    "prompt": prompt,
                    "sub_agents": [],
                    "output_key": "",
                    "custom_type": "",
                    "order": len(agents) + 1,
                })

    return {"system": system, "agents": agents}


def _to_v3_definition(parsed: dict[str, Any]) -> dict[str, Any]:
    """Normalize any TOML format (v1 legacy, v2 prompt-only, v3) to v3."""
    root = parsed if isinstance(parsed, dict) else {}

    # Detect format
    has_v3_agents = isinstance(root.get("agents"), list) and any(
        isinstance(a, dict) and "type" in a for a in root["agents"]
    )
    has_named_tables = isinstance(root.get("agents"), dict) or isinstance(root.get("workflow"), dict)
    has_v2_prompt_agents = isinstance(root.get("prompt_agents"), list)
    has_legacy_agents = isinstance(root.get("agents"), list) and any(
        isinstance(a, dict) and ("instruction" in a or "instructions" in a) for a in root.get("agents", [])
    )

    raw_system = root.get("system") if isinstance(root.get("system"), dict) else {}
    system = {"entrypoint": str(raw_system.get("entrypoint") or "orchestrator").strip() or "orchestrator"}

    if has_named_tables:
        agents: list[dict[str, Any]] = []
        named_agents = _extract_agents_from_named_tables(root.get("agents"), default_type="llm")
        named_workflows = _extract_agents_from_named_tables(
            root.get("workflow"),
            default_type="sequential",
            order_offset=len(named_agents),
        )
        agents.extend(named_agents)
        agents.extend(named_workflows)
        return {"system": system, "agents": agents}

    if has_v3_agents:
        agents = _extract_agents_v3(root["agents"])
        # Allow hybrid TOML where [[agents]] and [workflow.*] coexist.
        if isinstance(root.get("workflow"), dict):
            workflow_agents = _extract_agents_from_named_tables(
                root.get("workflow"),
                default_type="sequential",
                order_offset=len(agents),
            )
            agents.extend(workflow_agents)
        return {"system": system, "agents": agents}

    if has_v2_prompt_agents or has_legacy_agents:
        return _migrate_v2_to_v3(parsed)

    # Fallback: try to extract whatever we can
    return _migrate_v2_to_v3(parsed)


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------

def _detect_cycles(agents: list[dict[str, Any]]) -> list[str]:
    """Detect circular dependencies in sub_agents graph. Returns error strings."""
    adjacency: dict[str, list[str]] = {}
    for agent in agents:
        agent_id = str(agent.get("id") or "")
        sub_agents = agent.get("sub_agents") or []
        adjacency[agent_id] = [str(s) for s in sub_agents]

    visited: set[str] = set()
    in_stack: set[str] = set()
    errors: list[str] = []

    def dfs(node: str, path: list[str]) -> None:
        if node in in_stack:
            cycle_start = path.index(node)
            cycle_path = " -> ".join(path[cycle_start:] + [node])
            errors.append(f"Circular dependency detected: {cycle_path}.")
            return
        if node in visited:
            return
        visited.add(node)
        in_stack.add(node)
        path.append(node)
        for neighbor in adjacency.get(node, []):
            dfs(neighbor, path)
        path.pop()
        in_stack.discard(node)

    for agent_id in adjacency:
        if agent_id not in visited:
            dfs(agent_id, [])

    return errors


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_definition(parsed: dict[str, Any], *, strict: bool) -> list[str]:
    errors: list[str] = []
    root = parsed if isinstance(parsed, dict) else {}
    normalized = _to_v3_definition(root)

    raw_system = root.get("system")
    if not isinstance(raw_system, dict):
        errors.append("Missing [system] table.")
        raw_system = {}

    raw_entrypoint = raw_system.get("entrypoint")
    if not isinstance(raw_entrypoint, str) or not raw_entrypoint.strip():
        errors.append("Field 'system.entrypoint' is required.")

    if normalized["system"]["entrypoint"] != "orchestrator":
        errors.append("Entrypoint must be 'orchestrator'.")

    agents = normalized["agents"]
    if not agents:
        errors.append("At least one [[agents]] entry is required.")

    if len(agents) > MAX_DEFINITION_AGENTS:
        errors.append(f"Definition exceeds max agents: {len(agents)} > {MAX_DEFINITION_AGENTS}.")

    all_ids: set[str] = set()
    seen_ids: set[str] = set()

    # Collect all agent IDs first for sub_agent reference validation
    for agent in agents:
        agent_id = str(agent.get("id") or "").strip()
        if agent_id:
            all_ids.add(agent_id)

    for agent in agents:
        agent_id = str(agent.get("id") or "").strip()
        agent_type = str(agent.get("type") or "llm")
        prompt = str(agent.get("prompt") or "")
        name = str(agent.get("name") or "").strip()
        custom_type = str(agent.get("custom_type") or "").strip()
        sub_agents = agent.get("sub_agents") or []

        if not agent_id:
            errors.append("Agent entry missing required 'id'.")
            continue

        if not AGENT_ID_RE.match(agent_id):
            errors.append(f"Agent '{agent_id}' must be snake_case.")

        if agent_id in RESERVED_AGENT_IDS:
            errors.append(f"Agent id '{agent_id}' is reserved.")

        if agent_id in seen_ids:
            errors.append(f"Duplicate agent id '{agent_id}'.")
        seen_ids.add(agent_id)

        if agent_type not in VALID_AGENT_TYPES:
            errors.append(f"Agent '{agent_id}': invalid type '{agent_type}'. Must be one of: {', '.join(sorted(VALID_AGENT_TYPES))}.")

        if agent_type == "llm" and not prompt.strip():
            errors.append(f"Agent '{agent_id}': 'prompt' is required for type 'llm'.")

        if agent_type in ("sequential", "parallel", "loop"):
            if not sub_agents:
                errors.append(f"Agent '{agent_id}': 'sub_agents' required and non-empty for type '{agent_type}'.")

        if agent_type == "custom":
            custom_ref = custom_type or name or agent_id
            if not custom_ref.strip():
                errors.append(f"Agent '{agent_id}': custom agent reference is required.")
            elif _resolve_custom_agent_factory(custom_ref) is None:
                errors.append(
                    f"Agent '{agent_id}': custom agent '{custom_ref}' is not registered in backend."
                )

        # Validate sub_agent references
        for ref in sub_agents:
            ref_str = str(ref).strip()
            if ref_str and ref_str not in all_ids:
                errors.append(f"Agent '{agent_id}': sub_agent '{ref_str}' does not exist.")

    # Cycle detection
    if agents and not errors:
        cycle_errors = _detect_cycles(agents)
        errors.extend(cycle_errors)

    if strict:
        # Detect schema version from raw TOML
        is_v2 = "prompt_agents" in root and "agents" not in root

        if is_v2:
            # v2 strict validation (backward compat for existing stored docs)
            for key in root:
                if key not in LEGACY_ROOT_ALLOWED_FIELDS:
                    errors.append(f"Field '{key}' is not allowed in prompt-only schema.")

            if isinstance(raw_system, dict):
                for key in raw_system:
                    if key not in LEGACY_SYSTEM_ALLOWED_FIELDS:
                        errors.append(f"Field 'system.{key}' is not allowed in prompt-only schema.")

            raw_prompt_agents = root.get("prompt_agents")
            if isinstance(raw_prompt_agents, list):
                for index, raw_agent in enumerate(raw_prompt_agents, start=1):
                    if not isinstance(raw_agent, dict):
                        errors.append(f"Entry [[prompt_agents]] #{index} must be a table.")
                        continue
                    for key in raw_agent:
                        if key not in LEGACY_PROMPT_AGENT_ALLOWED_FIELDS:
                            errors.append(
                                f"Field 'prompt_agents[{index}].{key}' is not allowed in prompt-only schema."
                            )
                    raw_order = raw_agent.get("order")
                    if raw_order is not None and not isinstance(raw_order, int):
                        errors.append(f"Field 'prompt_agents[{index}].order' must be an integer.")
        else:
            # v3 strict validation
            for key in root:
                if key not in ROOT_ALLOWED_FIELDS:
                    errors.append(f"Field '{key}' is not allowed.")

            if isinstance(raw_system, dict):
                for key in raw_system:
                    if key not in SYSTEM_ALLOWED_FIELDS:
                        errors.append(f"Field 'system.{key}' is not allowed.")

            raw_agents = root.get("agents")
            if isinstance(raw_agents, list):
                for index, raw_agent in enumerate(raw_agents, start=1):
                    if not isinstance(raw_agent, dict):
                        errors.append(f"Entry [[agents]] #{index} must be a table.")
                        continue
                    for key in raw_agent:
                        if key not in AGENT_ALLOWED_FIELDS:
                            errors.append(f"Field 'agents[{index}].{key}' is not allowed.")
                    raw_order = raw_agent.get("order")
                    if raw_order is not None and not isinstance(raw_order, int):
                        errors.append(f"Field 'agents[{index}].order' must be an integer.")

    return errors


def _extract_denormalized_fields(parsed: dict[str, Any]) -> dict[str, Any]:
    normalized = _to_v3_definition(parsed)
    system = normalized.get("system", {})
    agents = normalized.get("agents", [])

    agent_ids: list[str] = []
    for agent in agents:
        agent_id = str(agent.get("id") or "").strip()
        if agent_id:
            agent_ids.append(agent_id)

    return {
        "entrypoint": str(system.get("entrypoint") or "").strip(),
        "agent_count": len(agent_ids),
        "agent_ids": agent_ids,
        # Legacy compat fields
        "prompt_agent_count": len(agent_ids),
        "prompt_agent_ids": agent_ids,
    }


def _stringify_v3_definition(parsed: dict[str, Any]) -> str:
    """Serialize a v3 definition to TOML string."""
    normalized = _to_v3_definition(parsed)
    system = normalized["system"]

    agents = sorted(
        normalized["agents"],
        key=lambda item: (int(item.get("order") or 0), str(item.get("id") or "")),
    )

    lines = [
        "[system]",
        f"entrypoint = {json.dumps(str(system.get('entrypoint') or 'orchestrator'), ensure_ascii=False)}",
    ]

    for agent in agents:
        agent_id = str(agent.get("id") or "")
        name = str(agent.get("name") or agent_id)
        agent_type = str(agent.get("type") or "llm")
        model = str(agent.get("model") or "")
        description = str(agent.get("description") or "")
        prompt = str(agent.get("prompt") or "")
        sub_agents = agent.get("sub_agents") or []
        output_key = str(agent.get("output_key") or "")
        custom_type = str(agent.get("custom_type") or "")
        order = int(agent.get("order") or 0)

        lines.extend([
            "",
            "[[agents]]",
            f"id = {json.dumps(agent_id, ensure_ascii=False)}",
            f"name = {json.dumps(name, ensure_ascii=False)}",
            f"type = {json.dumps(agent_type, ensure_ascii=False)}",
            f"model = {json.dumps(model, ensure_ascii=False)}",
            f"description = {json.dumps(description, ensure_ascii=False)}",
            f"prompt = {json.dumps(prompt, ensure_ascii=False)}",
            f"sub_agents = [{', '.join(json.dumps(s, ensure_ascii=False) for s in sub_agents)}]",
            f"output_key = {json.dumps(output_key, ensure_ascii=False)}",
        ])

        if custom_type or agent_type == "custom":
            lines.append(f"custom_type = {json.dumps(custom_type, ensure_ascii=False)}")

        lines.append(f"order = {order}")

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
class AgentConfig:
    agent_id: str
    name: str
    agent_type: str
    model: str
    description: str
    prompt: str
    custom_type: str = ""
    sub_agents: list[str] = field(default_factory=list)
    output_key: str = ""
    order: int = 0


def _resolve_consensus_output_key(cfg: AgentConfig) -> str:
    candidate = cfg.output_key.strip() if isinstance(cfg.output_key, str) else ""
    if not candidate:
        candidate = f"{cfg.agent_id}_output"

    candidate = re.sub(r"\s+", "_", candidate)
    candidate = re.sub(r"[^a-zA-Z0-9_]", "", candidate)
    if not candidate:
        candidate = f"{cfg.agent_id}_output"
    return candidate


def _resolve_consensus_participant_prompt(cfg: AgentConfig) -> str:
    prompt = cfg.prompt.strip() if isinstance(cfg.prompt, str) else ""
    if prompt:
        return prompt

    description = cfg.description.strip() if isinstance(cfg.description, str) else ""
    if description:
        return description

    display_name = (cfg.name or cfg.agent_id).strip() or cfg.agent_id
    return (
        f"Eres {display_name}. "
        "Analiza la pregunta del atleta y aporta una recomendacion accionable."
    )


def _build_consensus_participant_instruction(
    *,
    cfg: AgentConfig,
    participants: list[AgentConfig],
    output_keys_by_agent: dict[str, str],
    round_index: int,
    total_rounds: int,
) -> str:
    base_prompt = _resolve_consensus_participant_prompt(cfg)

    # In round 1 no agent has run yet, so no output keys exist in session state.
    # Using {key} template vars when the key is absent causes ADK to raise
    # "Context variable not found: <key>".  Only inject live references from
    # round 2 onward, when all participants have already written their outputs.
    if round_index > 1:
        shared_state_lines = []
        for participant in participants:
            key = output_keys_by_agent[participant.agent_id]
            shared_state_lines.append(f"- {participant.agent_id} ({key}): {{{key}}}")
        shared_state_block = "\n".join(shared_state_lines)
    else:
        shared_state_block = "(Sin aportaciones previas — primera ronda)"

    return (
        f"{base_prompt}\n\n"
        "Modo: consenso multi-agente.\n"
        f"Ronda: {round_index}/{total_rounds}.\n"
        f"Debes guardar tu resultado final en output_key '{output_keys_by_agent[cfg.agent_id]}'.\n"
        "Usa el estado compartido disponible para refinar tu postura.\n"
        "Estado compartido (puede estar vacio al inicio):\n"
        f"{shared_state_block}\n"
        "Responde en espanol, breve, y agrega solo informacion util para el consenso final."
    )


def _build_consensus_finalizer_instruction(
    *,
    participants: list[AgentConfig],
    output_keys_by_agent: dict[str, str],
    total_rounds: int,
) -> str:
    participant_lines = []
    for participant in participants:
        key = output_keys_by_agent[participant.agent_id]
        display_name = (participant.name or participant.agent_id).strip() or participant.agent_id
        participant_lines.append(f"- {display_name} [{key}]: {{{key}}}")
    participant_block = "\n".join(participant_lines)

    return (
        "Eres el sintetizador final de consenso para el atleta.\n"
        f"Recibes los aportes despues de {total_rounds} rondas de iteracion de los siguientes agentes:\n"
        f"{participant_block}\n\n"
        "ESTRUCTURA OBLIGATORIA de tu respuesta:\n"
        "1. **Perspectivas de los agentes**: Resume brevemente lo que aportó cada agente (1-2 frases por agente, "
        "usa el nombre del agente como encabezado). Si dos agentes coinciden en algo, señálalo. "
        "Si hay diferencias o énfasis distintos, menciónalos.\n"
        "2. **Recomendación final**: Integra los aportes en una respuesta clara, accionable y personalizada. "
        "Si hay desacuerdos, explica cuál es la mejor opción y por qué.\n\n"
        "Responde en español. No inventes datos que no estén en los aportes."
    )


class AgentDefinitionStore:
    """Persistence for agent TOML definitions."""

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
                    "agent_count": denormalized["agent_count"],
                    "agent_ids": denormalized["agent_ids"],
                    "prompt_agent_count": denormalized["prompt_agent_count"],
                    "prompt_agent_ids": denormalized["prompt_agent_ids"],
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
            "agent_count": denormalized["agent_count"],
            "agent_ids": denormalized["agent_ids"],
            "prompt_agent_count": denormalized["prompt_agent_count"],
            "prompt_agent_ids": denormalized["prompt_agent_ids"],
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


def _agent_configs(parsed: dict[str, Any]) -> list[AgentConfig]:
    normalized = _to_v3_definition(parsed)
    agents = sorted(
        normalized["agents"],
        key=lambda item: (int(item.get("order") or 0), str(item.get("id") or "")),
    )

    configs: list[AgentConfig] = []
    for agent in agents:
        agent_id = str(agent.get("id") or "").strip()
        name = str(agent.get("name") or agent_id).strip() or agent_id
        agent_type = str(agent.get("type") or "llm")
        model = str(agent.get("model") or "")
        description = str(agent.get("description") or "")
        prompt = str(agent.get("prompt") or "")
        custom_type = str(agent.get("custom_type") or "").strip()
        sub_agents = agent.get("sub_agents") or []
        output_key = str(agent.get("output_key") or "")
        order = int(agent.get("order") or 0)

        if not agent_id:
            continue

        configs.append(AgentConfig(
            agent_id=agent_id,
            name=name,
            agent_type=agent_type,
            model=model,
            description=description,
            prompt=prompt,
            custom_type=custom_type,
            sub_agents=[str(s) for s in sub_agents],
            output_key=output_key,
            order=order,
        ))

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
            normalized = _to_v3_definition(parsed)
            errors = _validate_definition(normalized, strict=False)
            if errors:
                raise AgentDefinitionValidationError("; ".join(errors))
            toml_content = _stringify_v3_definition(normalized)
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

        normalized = _to_v3_definition(parsed)
        normalized_toml = _stringify_v3_definition(normalized)

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
            normalized = _to_v3_definition(parsed)
            errors = _validate_definition(normalized, strict=False)
            if errors:
                return None

            target_id = (agent_id or WIKI_RESEARCH_CHAT_AGENT_ID).strip() or WIKI_RESEARCH_CHAT_AGENT_ID
            for agent in normalized.get("agents", []):
                if not isinstance(agent, dict):
                    continue
                if str(agent.get("id") or "").strip() != target_id:
                    continue
                candidate = str(agent.get("prompt") or "").strip()
                if candidate:
                    return candidate

            # Fall back to first non-reserved agent in the TOML definition.
            # This handles the single-agent case where the user defined one custom
            # agent — its prompt should influence the wiki chat response.
            for agent in normalized.get("agents", []):
                if not isinstance(agent, dict):
                    continue
                agent_id_in_toml = str(agent.get("id") or "").strip()
                if agent_id_in_toml in RESERVED_AGENT_IDS:
                    continue
                candidate = str(agent.get("prompt") or "").strip()
                if candidate:
                    return candidate

            return None
        except Exception:  # noqa: BLE001
            return None

    def build_wiki_consensus_agent(
        self,
        *,
        athlete_id: str | int,
        wiki_context_block: str,
        model_name: str | None = None,
    ) -> LlmAgent | None:
        """Build a consensus pipeline from the athlete's custom TOML agents.

        Each custom agent gets wiki context prepended to its prompt.
        Returns ``None`` when the athlete has no custom definition or only one
        (or zero) non-reserved agents — the caller should fall back to the
        single-agent path.
        """
        normalized_athlete_id = str(athlete_id).strip()
        if not normalized_athlete_id:
            return None

        entry = self._store.get(normalized_athlete_id)
        if entry.is_default:
            return None

        try:
            parsed = _parse_toml(entry.toml_content)
            normalized = _to_v3_definition(parsed)
            errors = _validate_definition(normalized, strict=False)
            if errors:
                return None
        except Exception:  # noqa: BLE001
            return None

        configs = _agent_configs(normalized)
        participants = [cfg for cfg in configs if cfg.agent_id not in RESERVED_AGENT_IDS]
        if len(participants) < 2:
            return None

        fallback_model_name = model_name.strip() if isinstance(model_name, str) and model_name.strip() else None
        default_model = get_llm_provider(model_name=fallback_model_name)

        output_keys_by_agent = {
            cfg.agent_id: _resolve_consensus_output_key(cfg)
            for cfg in participants
        }

        loop_sub_agents: list[Any] = []
        for round_index in range(1, CONSENSUS_ITERATIONS + 1):
            for cfg in participants:
                agent_model_name = cfg.model.strip() if cfg.model.strip() else fallback_model_name
                agent_model = get_llm_provider(model_name=agent_model_name)
                base_instruction = _build_consensus_participant_instruction(
                    cfg=cfg,
                    participants=participants,
                    output_keys_by_agent=output_keys_by_agent,
                    round_index=round_index,
                    total_rounds=CONSENSUS_ITERATIONS,
                )
                participant_instruction = wiki_context_block + base_instruction
                loop_sub_agents.append(
                    LlmAgent(
                        name=f"{cfg.agent_id}_round_{round_index}",
                        model=agent_model,
                        instruction=participant_instruction,
                        description=cfg.description or cfg.name or cfg.agent_id,
                        tools=[],
                        output_key=output_keys_by_agent[cfg.agent_id],
                    )
                )

        consensus_finalizer = LlmAgent(
            name="consensus_finalizer",
            model=default_model,
            instruction=_build_consensus_finalizer_instruction(
                participants=participants,
                output_keys_by_agent=output_keys_by_agent,
                total_rounds=CONSENSUS_ITERATIONS,
            ),
            description="Build one final answer from all participant outputs.",
            tools=[],
            output_key="consensus_final_answer",
        )

        consensus_pipeline = SequentialAgent(
            name="wiki_consensus_pipeline",
            sub_agents=[*loop_sub_agents, consensus_finalizer],
            description=(
                f"Wiki multi-agent consensus loop. "
                f"rounds={CONSENSUS_ITERATIONS}, participants={len(participants)}"
            ),
        )

        # Wrap in a root LlmAgent so the runner can invoke it the same way
        # as the single wiki_research_chat_agent.
        return LlmAgent(
            name="wiki_multi_agent",
            model=default_model,
            instruction=(
                "Eres el coordinador del consenso multi-agente para el atleta.\n"
                "REGLA ABSOLUTA: ante CUALQUIER mensaje del usuario — saludo, pregunta deportiva, "
                "solicitud de entrenamiento, consulta sobre rendimiento o cualquier otro tema — "
                "SIEMPRE debes invocar wiki_consensus_pipeline sin excepcion.\n"
                "Nunca rechaces una pregunta ni respondas directamente sin invocar primero wiki_consensus_pipeline.\n"
                "Tras obtener el resultado del pipeline, devuelve su respuesta final al usuario en texto claro en espanol."
            ),
            tools=[AgentTool(agent=consensus_pipeline)],
        )

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
            normalized = _to_v3_definition(parsed)
            errors = _validate_definition(normalized, strict=False)
            if errors:
                raise AgentDefinitionValidationError("; ".join(errors))
        except Exception:
            if entry.is_default:
                normalized = _to_v3_definition(_parse_toml(DEFAULT_TOML_TEMPLATE))
            else:
                logger.warning(
                    "Invalid custom agent definition for athlete %s. Falling back to default template.",
                    entry.athlete_id,
                )
                normalized = _to_v3_definition(_parse_toml(DEFAULT_TOML_TEMPLATE))

        runtime_planner_mode = _normalize_planner_mode(planner_mode, fallback="full_only")

        # Default model for system agents
        fallback_model_name = model_name.strip() if isinstance(model_name, str) and model_name.strip() else None
        default_model = get_llm_provider(model_name=fallback_model_name)

        # ── Build system (internal) agents ──────────────────────────────

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
            model=default_model,
            instruction=intent_router_instruction,
            tools=[],
        )

        planner_kwargs = _build_plan_react_kwargs(enabled=runtime_planner_mode != "off")
        try:
            plan_react_planner = LlmAgent(
                name="plan_react_planner",
                model=default_model,
                instruction=plan_react_instruction,
                tools=[],
                **planner_kwargs,
            )
        except TypeError:
            plan_react_planner = LlmAgent(
                name="plan_react_planner",
                model=default_model,
                instruction=plan_react_instruction,
                tools=[],
            )

        strava_ingestion_agent = LlmAgent(
            name="strava_ingestion_agent",
            model=default_model,
            instruction=ingestion_instruction,
            tools=[TOOL_CATALOG["run_ingestion_pipeline"]],
        )

        query_agent = LlmAgent(
            name="query_agent",
            model=default_model,
            instruction=query_instruction,
            tools=[TOOL_CATALOG["run_query_pipeline"]],
        )

        answer_agent = LlmAgent(
            name="answer_agent",
            model=default_model,
            instruction=answer_instruction,
            tools=[],
        )

        # ── Build consensus loop from user-defined agents ───────────────

        configs = _agent_configs(normalized)
        participants = [cfg for cfg in configs if cfg.agent_id not in RESERVED_AGENT_IDS]

        custom_tools: list[AgentTool] = []
        root_custom_ids: list[str] = []

        if participants:
            output_keys_by_agent = {
                cfg.agent_id: _resolve_consensus_output_key(cfg)
                for cfg in participants
            }

            loop_sub_agents: list[Any] = []
            for round_index in range(1, CONSENSUS_ITERATIONS + 1):
                for cfg in participants:
                    agent_model_name = cfg.model.strip() if cfg.model.strip() else fallback_model_name
                    agent_model = get_llm_provider(model_name=agent_model_name)
                    participant_instruction = _build_consensus_participant_instruction(
                        cfg=cfg,
                        participants=participants,
                        output_keys_by_agent=output_keys_by_agent,
                        round_index=round_index,
                        total_rounds=CONSENSUS_ITERATIONS,
                    )
                    loop_sub_agents.append(
                        LlmAgent(
                            name=f"{cfg.agent_id}_round_{round_index}",
                            model=agent_model,
                            instruction=participant_instruction,
                            description=cfg.description or cfg.name or cfg.agent_id,
                            tools=[],
                            output_key=output_keys_by_agent[cfg.agent_id],
                        )
                    )

            consensus_finalizer = LlmAgent(
                name="consensus_finalizer",
                model=default_model,
                instruction=_build_consensus_finalizer_instruction(
                    participants=participants,
                    output_keys_by_agent=output_keys_by_agent,
                    total_rounds=CONSENSUS_ITERATIONS,
                ),
                description="Build one final answer from all participant outputs.",
                tools=[],
                output_key="consensus_final_answer",
            )

            consensus_pipeline = SequentialAgent(
                name=CONSENSUS_TOOL_ID,
                sub_agents=[*loop_sub_agents, consensus_finalizer],
                description=(
                    "Deterministic multi-agent consensus loop over user-defined agents. "
                    f"rounds={CONSENSUS_ITERATIONS}, participants={len(participants)}"
                ),
            )

            custom_tools.append(AgentTool(agent=consensus_pipeline))
            root_custom_ids = [cfg.agent_id for cfg in participants]

        # ── Compose orchestrator ────────────────────────────────────────

        orchestrator_tools: list[Any] = [
            AgentTool(agent=intent_router),
        ]
        if runtime_planner_mode != "off":
            orchestrator_tools.append(AgentTool(agent=plan_react_planner))

        orchestrator_tools.extend([
            AgentTool(agent=strava_ingestion_agent),
            AgentTool(agent=query_agent),
            AgentTool(agent=answer_agent),
            *custom_tools,
        ])

        orchestrator_instruction = _build_default_orchestrator_instruction(
            runtime_planner_mode,
            custom_agent_ids=root_custom_ids,
            consensus_tool_id=CONSENSUS_TOOL_ID if custom_tools else None,
            consensus_rounds=CONSENSUS_ITERATIONS,
        )

        return LlmAgent(
            name="orchestrator",
            model=default_model,
            instruction=orchestrator_instruction,
            tools=orchestrator_tools,
        )
