import asyncio
import uuid
import json
import logging
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import firebase_admin
    from firebase_admin import firestore as firebase_firestore
except Exception:  # noqa: BLE001
    firebase_admin = None
    firebase_firestore = None

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types


logger = logging.getLogger(__name__)

APP_NAME = "multi_agent_executor"
USER_ID = "user1"
_STREAM_CHUNK_SIZE = 180
DEFAULT_AGENT_CHAIN_LOGS_COLLECTION = "agent_definition_logs"
DEFAULT_AGENT_CHAIN_LOG_HISTORY_LIMIT = 15
DEFAULT_AGENT_CHAIN_LOG_EVENT_LIMIT = 80
DEFAULT_AGENT_CHAIN_LOG_TEXT_LIMIT = 260
DEFAULT_AGENT_CHAIN_LOG_TOOL_CALL_LIMIT = 60

_PLAN_REACT_SECTION_TAGS: tuple[tuple[str, str], ...] = (
    ("planning", "PLANNING"),
    ("reasoning", "REASONING"),
    ("action", "ACTION"),
    ("observation", "OBSERVATION"),
    ("replanning", "REPLANNING"),
    ("final_answer", "FINAL_ANSWER"),
)
_PLAN_REACT_SECTIONS_ORDER = [section for section, _ in _PLAN_REACT_SECTION_TAGS]
_PLAN_REACT_TAG_CLEAN_RE = re.compile(
    r"</?(?:PLANNING|REASONING|ACTION|OBSERVATION|REPLANNING|FINAL_ANSWER)>",
    flags=re.IGNORECASE,
)


def _read_positive_int_env(var_name: str, default: int) -> int:
    raw_value = os.environ.get(var_name)
    if raw_value is None:
        return default

    try:
        parsed = int(str(raw_value).strip())
        return parsed if parsed > 0 else default
    except (TypeError, ValueError):
        return default


_AGENT_CHAIN_LOG_HISTORY_LIMIT = _read_positive_int_env(
    "FIRESTORE_AGENT_CHAIN_LOG_HISTORY_LIMIT",
    DEFAULT_AGENT_CHAIN_LOG_HISTORY_LIMIT,
)
_AGENT_CHAIN_LOG_EVENT_LIMIT = _read_positive_int_env(
    "FIRESTORE_AGENT_CHAIN_LOG_EVENT_LIMIT",
    DEFAULT_AGENT_CHAIN_LOG_EVENT_LIMIT,
)
_AGENT_CHAIN_LOG_TEXT_LIMIT = _read_positive_int_env(
    "FIRESTORE_AGENT_CHAIN_LOG_TEXT_LIMIT",
    DEFAULT_AGENT_CHAIN_LOG_TEXT_LIMIT,
)
_AGENT_CHAIN_LOG_TOOL_CALL_LIMIT = _read_positive_int_env(
    "FIRESTORE_AGENT_CHAIN_LOG_TOOL_CALL_LIMIT",
    DEFAULT_AGENT_CHAIN_LOG_TOOL_CALL_LIMIT,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentChainLogStore:
    """Persist conversation traces under agent_definition_logs/{athlete_id}."""

    def __init__(self) -> None:
        self._collection = (
            os.environ.get("FIRESTORE_AGENT_DEFINITION_LOGS_COLLECTION")
            or os.environ.get("FIRESTORE_AGENT_CHAIN_LOG_COLLECTION")
            or DEFAULT_AGENT_CHAIN_LOGS_COLLECTION
        ).strip() or DEFAULT_AGENT_CHAIN_LOGS_COLLECTION
        self._history_limit = _AGENT_CHAIN_LOG_HISTORY_LIMIT
        self._state_path = Path(tempfile.gettempdir()) / "strava_agent_state" / "agent_definition_logs.json"
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

    def _load_local_state(self) -> dict[str, Any]:
        if not self._state_path.exists():
            return {}

        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_local_state(self, payload: dict[str, Any]) -> None:
        self._state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _get_firestore_payload(self, athlete_id: str) -> dict[str, Any] | None:
        if self._client is None:
            return None

        try:
            doc = self._client.collection(self._collection).document(athlete_id).get()
            if doc.exists:
                payload = doc.to_dict()
                return payload if isinstance(payload, dict) else {}
            return {}
        except Exception:  # noqa: BLE001
            logger.warning(
                "Failed to read agent chain log from Firestore for athlete_id=%s. Falling back to local state.",
                athlete_id,
            )
            self._client = None
            return None

    def _get_local_payload(self, athlete_id: str) -> dict[str, Any]:
        local_state = self._load_local_state()
        payload = local_state.get(athlete_id)
        return payload if isinstance(payload, dict) else {}

    def _get_payload(self, athlete_id: str) -> dict[str, Any]:
        firestore_payload = self._get_firestore_payload(athlete_id)
        if isinstance(firestore_payload, dict):
            return firestore_payload
        return self._get_local_payload(athlete_id)

    def _trim_conversations(self, conversations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(conversations) <= self._history_limit:
            return conversations
        return conversations[-self._history_limit :]

    def _build_document_payload(
        self,
        *,
        athlete_id: str,
        conversations: list[dict[str, Any]],
        previous: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        previous = previous if isinstance(previous, dict) else {}
        first_seen_at = str(previous.get("first_seen_at") or _utc_now_iso())
        return {
            "athlete_id": athlete_id,
            "first_seen_at": first_seen_at,
            "updated_at": _utc_now_iso(),
            "conversation_count": len(conversations),
            "conversations": conversations,
        }

    def save_conversation(self, athlete_id: str | int, conversation: dict[str, Any]) -> bool:
        normalized_athlete_id = str(athlete_id or "").strip()
        if not normalized_athlete_id:
            return False

        normalized_conversation = dict(conversation)
        normalized_conversation.setdefault("created_at", _utc_now_iso())

        if self._client is not None:
            try:
                doc_ref = self._client.collection(self._collection).document(normalized_athlete_id)
                existing_doc = doc_ref.get()
                existing_payload = existing_doc.to_dict() if existing_doc.exists else {}
                existing_conversations = existing_payload.get("conversations")
                if not isinstance(existing_conversations, list):
                    existing_conversations = []

                existing_conversations.append(normalized_conversation)
                trimmed = self._trim_conversations([
                    item for item in existing_conversations if isinstance(item, dict)
                ])
                doc_ref.set(
                    self._build_document_payload(
                        athlete_id=normalized_athlete_id,
                        conversations=trimmed,
                        previous=existing_payload,
                    )
                )
                return True
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Failed to persist agent chain log in Firestore for athlete_id=%s. Falling back to local state.",
                    normalized_athlete_id,
                )
                self._client = None

        local_state = self._load_local_state()
        athlete_payload = local_state.get(normalized_athlete_id)
        if not isinstance(athlete_payload, dict):
            athlete_payload = {}

        existing_conversations = athlete_payload.get("conversations")
        if not isinstance(existing_conversations, list):
            existing_conversations = []

        existing_conversations.append(normalized_conversation)
        trimmed = self._trim_conversations([
            item for item in existing_conversations if isinstance(item, dict)
        ])

        local_state[normalized_athlete_id] = self._build_document_payload(
            athlete_id=normalized_athlete_id,
            conversations=trimmed,
            previous=athlete_payload,
        )
        self._save_local_state(local_state)
        return True

    def get_conversations(
        self,
        athlete_id: str | int,
        *,
        page: int = 1,
        page_size: int = 5,
        include_events: bool = False,
    ) -> dict[str, Any]:
        normalized_athlete_id = str(athlete_id or "").strip()
        if not normalized_athlete_id:
            return {
                "athlete_id": "",
                "page": 1,
                "page_size": max(1, page_size),
                "total_conversations": 0,
                "total_pages": 0,
                "has_next": False,
                "has_previous": False,
                "conversations": [],
            }

        resolved_page = max(1, int(page))
        resolved_page_size = max(1, min(int(page_size), 50))

        payload = self._get_payload(normalized_athlete_id)
        raw_conversations = payload.get("conversations")
        if not isinstance(raw_conversations, list):
            raw_conversations = []

        valid_conversations = [item for item in raw_conversations if isinstance(item, dict)]
        ordered_conversations = list(reversed(valid_conversations))

        total_conversations = len(ordered_conversations)
        total_pages = int(math.ceil(total_conversations / resolved_page_size)) if total_conversations > 0 else 0

        start_index = (resolved_page - 1) * resolved_page_size
        end_index = start_index + resolved_page_size
        paged_conversations = ordered_conversations[start_index:end_index]

        normalized_conversations: list[dict[str, Any]] = []
        for conversation in paged_conversations:
            conversation_payload = json.loads(json.dumps(conversation, ensure_ascii=False))

            if not include_events:
                attempts = conversation_payload.get("attempts")
                if isinstance(attempts, list):
                    for attempt in attempts:
                        if isinstance(attempt, dict):
                            attempt.pop("events", None)

            normalized_conversations.append(conversation_payload)

        return {
            "athlete_id": normalized_athlete_id,
            "page": resolved_page,
            "page_size": resolved_page_size,
            "total_conversations": total_conversations,
            "total_pages": total_pages,
            "has_next": resolved_page < total_pages,
            "has_previous": resolved_page > 1 and total_pages > 0,
            "conversations": normalized_conversations,
        }


_AGENT_CHAIN_LOG_STORE = AgentChainLogStore()


def get_conversation_chain_logs(
    athlete_id: str | int,
    *,
    page: int = 1,
    page_size: int = 5,
    include_events: bool = False,
) -> dict[str, Any]:
    return _AGENT_CHAIN_LOG_STORE.get_conversations(
        athlete_id,
        page=page,
        page_size=page_size,
        include_events=include_events,
    )


def _truncate_for_log(value: str, *, limit: int = _AGENT_CHAIN_LOG_TEXT_LIMIT) -> str:
    cleaned = str(value or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[:limit]}..."


def _stringify_for_log(value: object, *, limit: int = _AGENT_CHAIN_LOG_TEXT_LIMIT) -> str:
    plain = _to_plain_value(value)
    if plain is None:
        return ""

    if isinstance(plain, (dict, list)):
        try:
            rendered = json.dumps(plain, ensure_ascii=False)
        except TypeError:
            rendered = str(plain)
        return _truncate_for_log(rendered, limit=limit)

    return _truncate_for_log(str(plain), limit=limit)


def _normalize_tool_calls_for_log(tool_calls: list[dict[str, object]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for call in tool_calls[:_AGENT_CHAIN_LOG_TOOL_CALL_LIMIT]:
        normalized_call: dict[str, str] = {
            "tool": _truncate_for_log(str(call.get("tool") or "tool")),
        }
        agent_name = _truncate_for_log(str(call.get("agent") or ""))
        if agent_name:
            normalized_call["agent"] = agent_name

        args_value = call.get("args")
        if args_value is not None:
            rendered_args = _stringify_for_log(args_value)
            if rendered_args:
                normalized_call["args"] = rendered_args

        normalized.append(normalized_call)
    return normalized


def _normalize_tool_outputs_for_log(outputs: list[dict[str, str]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for output in outputs[:_AGENT_CHAIN_LOG_TOOL_CALL_LIMIT]:
        normalized_output = {
            "tool": _truncate_for_log(output.get("tool", "tool")),
            "output": _truncate_for_log(output.get("output", "")),
        }
        agent_name = _truncate_for_log(output.get("agent", ""))
        if agent_name:
            normalized_output["agent"] = agent_name
        normalized.append(normalized_output)
    return normalized


def _event_is_final_response(event: object) -> bool:
    checker = getattr(event, "is_final_response", None)
    if not callable(checker):
        return False
    try:
        return bool(checker())
    except Exception:  # noqa: BLE001
        return False


def _append_agent_to_chain(chain: list[str], value: object) -> None:
    agent_name = _truncate_for_log(str(_to_plain_value(value) or ""), limit=120)
    if not agent_name:
        return
    if not chain or chain[-1] != agent_name:
        chain.append(agent_name)


def _merge_agent_chain(target: list[str], incoming: list[str]) -> None:
    for entry in incoming:
        _append_agent_to_chain(target, entry)


def _build_trace_event(
    *,
    index: int,
    event: object,
    event_tool_calls: list[dict[str, object]],
) -> dict[str, object]:
    content = getattr(event, "content", None)
    author = _truncate_for_log(str(_to_plain_value(getattr(event, "author", None)) or ""), limit=120)
    text = _truncate_for_log(_extract_text(content))
    tool_outputs = _normalize_tool_outputs_for_log(_extract_tool_output_events(content, event))

    trace_event: dict[str, object] = {
        "index": index,
        "final_response": _event_is_final_response(event),
    }
    if author:
        trace_event["author"] = author
    if text:
        trace_event["text"] = text
    if event_tool_calls:
        trace_event["tool_calls"] = _normalize_tool_calls_for_log(event_tool_calls)
    if tool_outputs:
        trace_event["tool_outputs"] = tool_outputs
    return trace_event


def _create_attempt_trace_context(session_id: str) -> dict[str, object]:
    return {
        "session_id": session_id,
        "events": [],
        "agent_chain": [],
        "dropped_events": 0,
    }


def _record_trace_event(
    trace_context: dict[str, object] | None,
    *,
    event: object,
    event_tool_calls: list[dict[str, object]],
) -> None:
    if trace_context is None:
        return

    events = trace_context.get("events")
    if not isinstance(events, list):
        events = []
        trace_context["events"] = events

    chain = trace_context.get("agent_chain")
    if not isinstance(chain, list):
        chain = []
        trace_context["agent_chain"] = chain

    _append_agent_to_chain(chain, getattr(event, "author", None))
    for call in event_tool_calls:
        _append_agent_to_chain(chain, call.get("agent"))

    if len(events) < _AGENT_CHAIN_LOG_EVENT_LIMIT:
        events.append(
            _build_trace_event(
                index=len(events),
                event=event,
                event_tool_calls=event_tool_calls,
            )
        )
        return

    trace_context["dropped_events"] = int(trace_context.get("dropped_events") or 0) + 1


def _build_attempt_log(
    *,
    attempt: int,
    retry: bool,
    trace_context: dict[str, object] | None,
    tool_calls: list[dict[str, object]],
    response_text: str,
    succeeded: bool,
) -> dict[str, object]:
    events = []
    chain: list[str] = []
    dropped_events = 0
    session_id = ""

    if isinstance(trace_context, dict):
        raw_events = trace_context.get("events")
        if isinstance(raw_events, list):
            events = [item for item in raw_events if isinstance(item, dict)]
        raw_chain = trace_context.get("agent_chain")
        if isinstance(raw_chain, list):
            _merge_agent_chain(chain, [str(item) for item in raw_chain])
        dropped_events = int(trace_context.get("dropped_events") or 0)
        session_id = _truncate_for_log(str(trace_context.get("session_id") or ""), limit=120)

    for call in tool_calls:
        _append_agent_to_chain(chain, call.get("agent"))

    return {
        "attempt": attempt,
        "retry": retry,
        "succeeded": succeeded,
        "session_id": session_id,
        "agent_chain": chain,
        "tool_calls": _normalize_tool_calls_for_log(tool_calls),
        "events": events,
        "dropped_events": dropped_events,
        "response_preview": _truncate_for_log(response_text),
    }


def _build_conversation_log(
    *,
    conversation_id: str,
    question: str,
    response_text: str,
    response_format: str | None,
    stream: bool,
    attempts: list[dict[str, object]],
    success: bool,
) -> dict[str, object]:
    combined_chain: list[str] = []
    combined_tool_calls: list[dict[str, str]] = []
    seen_tool_signatures: set[tuple[str, str, str]] = set()

    for attempt in attempts:
        attempt_chain = attempt.get("agent_chain")
        if isinstance(attempt_chain, list):
            _merge_agent_chain(combined_chain, [str(item) for item in attempt_chain])

        attempt_calls = attempt.get("tool_calls")
        if isinstance(attempt_calls, list):
            for call in attempt_calls:
                if not isinstance(call, dict):
                    continue
                signature = (
                    str(call.get("agent") or ""),
                    str(call.get("tool") or ""),
                    str(call.get("args") or ""),
                )
                if signature in seen_tool_signatures:
                    continue
                seen_tool_signatures.add(signature)
                combined_tool_calls.append({
                    "agent": _truncate_for_log(signature[0], limit=120),
                    "tool": _truncate_for_log(signature[1], limit=120),
                    "args": _truncate_for_log(signature[2]),
                })

    now_iso = _utc_now_iso()
    return {
        "conversation_id": _truncate_for_log(conversation_id, limit=120),
        "created_at": now_iso,
        "response_format": _normalize_response_format(response_format),
        "stream": bool(stream),
        "success": bool(success),
        "attempt_count": len(attempts),
        "retry_used": len(attempts) > 1,
        "question_preview": _truncate_for_log(question),
        "response_preview": _truncate_for_log(response_text),
        "agent_chain": combined_chain,
        "tool_calls": combined_tool_calls,
        "attempts": attempts,
    }


async def _persist_conversation_log(
    *,
    athlete_id: str | int | None,
    payload: dict[str, object],
) -> None:
    if athlete_id is None:
        return

    normalized_athlete_id = str(athlete_id).strip()
    if not normalized_athlete_id:
        return

    try:
        await asyncio.to_thread(
            _AGENT_CHAIN_LOG_STORE.save_conversation,
            normalized_athlete_id,
            payload,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "Failed to persist conversation chain log for athlete_id=%s.",
            normalized_athlete_id,
        )


def _normalize_response_format(response_format: str | None) -> str:
    if not isinstance(response_format, str):
        return "plan_react_v1"

    normalized = response_format.strip().lower()
    if normalized in {"plain", "plan_react_v1", "structured"}:
        return normalized
    return "plan_react_v1"


def _structured_output_enabled(response_format: str | None) -> bool:
    return _normalize_response_format(response_format) in {"plan_react_v1", "structured"}


def _extract_plan_react_sections(text: str) -> dict[str, list[str]]:
    if not text:
        return {}

    sections: dict[str, list[str]] = {}
    for section, tag in _PLAN_REACT_SECTION_TAGS:
        pattern = rf"<{tag}>\s*(.*?)\s*</{tag}>"
        matches = re.findall(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        cleaned_matches = [str(match).strip() for match in matches if str(match).strip()]
        if cleaned_matches:
            sections[section] = cleaned_matches

    return sections


def _strip_plan_react_tags(text: str) -> str:
    if not text:
        return ""

    without_tags = _PLAN_REACT_TAG_CLEAN_RE.sub("", text)
    normalized = re.sub(r"\n{3,}", "\n\n", without_tags)
    return normalized.strip()


def _format_tool_observation(entry: dict[str, str]) -> str:
    tool = entry.get("tool", "tool")
    agent = entry.get("agent", "")
    output = entry.get("output", "").strip()
    if len(output) > 600:
        output = f"{output[:600]}..."

    if agent:
        return f"[{agent}] {tool}: {output}"
    return f"{tool}: {output}"


def _build_structured_payload(
    text: str,
    tool_observations: list[str] | None = None,
) -> dict[str, object] | None:
    sections = _extract_plan_react_sections(text)

    if tool_observations:
        existing_observations = sections.get("observation", [])
        sections["observation"] = [*existing_observations, *tool_observations]

    if not sections:
        return None

    ordered_sections: dict[str, list[str]] = {}
    for section in _PLAN_REACT_SECTIONS_ORDER:
        values = sections.get(section)
        if values:
            ordered_sections[section] = values

    return {
        "format": "plan_react_v1",
        "sections": ordered_sections,
    }


def _extract_response_text(text: str, structured: dict[str, object] | None = None) -> str:
    if structured and isinstance(structured.get("sections"), dict):
        final_answer = structured["sections"].get("final_answer")
        if isinstance(final_answer, list) and final_answer:
            final_text = str(final_answer[-1]).strip()
            if final_text:
                return final_text

    stripped = _strip_plan_react_tags(text)
    if stripped:
        return stripped
    return text.strip()


def _collect_new_section_blocks(
    sections: dict[str, list[str]],
    emitted_counts: dict[str, int],
) -> list[dict[str, object]]:
    new_blocks: list[dict[str, object]] = []

    for section in _PLAN_REACT_SECTIONS_ORDER:
        values = sections.get(section, [])
        previous_count = emitted_counts.get(section, 0)
        for index in range(previous_count, len(values)):
            block_text = str(values[index]).strip()
            if not block_text:
                continue
            emitted_counts[section] = index + 1
            new_blocks.append(
                {
                    "section": section,
                    "text": block_text,
                    "index": index,
                }
            )

    return new_blocks


def _to_plain_value(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool, dict, list)):
        return value
    return str(value)


def _build_prompt_with_precomputed_context(question: str) -> str:
    return question


def _extract_text(content: object) -> str:
    if not content or not getattr(content, "parts", None):
        return ""

    chunks = []
    for part in content.parts:
        text = getattr(part, "text", None)
        if text:
            chunks.append(text)
    return "\n".join(chunks).strip()


def _normalize_tool_call_record(record: object, fallback_agent: object | None = None) -> dict[str, object] | None:
    if not isinstance(record, dict):
        return None

    tool = record.get("tool") or record.get("name")
    if not tool:
        return None

    normalized: dict[str, object] = {"tool": _to_plain_value(tool)}

    agent = record.get("agent") or record.get("author") or fallback_agent
    if agent:
        normalized["agent"] = _to_plain_value(agent)

    args = record.get("args")
    if args is None:
        args = record.get("arguments")
    if args is not None:
        normalized["args"] = _to_plain_value(args)

    return normalized


def _extract_nested_tool_calls(value: object, fallback_agent: object | None = None) -> list[dict[str, object]]:
    nested_calls: list[dict[str, object]] = []

    if isinstance(value, dict):
        candidate = _normalize_tool_call_record(value, fallback_agent)
        if candidate:
            nested_calls.append(candidate)

        embedded = value.get("tool_calls")
        if isinstance(embedded, list):
            for item in embedded:
                nested_calls.extend(_extract_nested_tool_calls(item, fallback_agent))

        for nested_value in value.values():
            if isinstance(nested_value, (dict, list)):
                nested_calls.extend(_extract_nested_tool_calls(nested_value, fallback_agent))

    elif isinstance(value, list):
        for item in value:
            nested_calls.extend(_extract_nested_tool_calls(item, fallback_agent))

    return nested_calls


def _extract_tool_calls(content: object, event: object | None = None) -> list[dict[str, object]]:
    if not content or not getattr(content, "parts", None):
        return []

    calls: list[dict[str, object]] = []
    author = _to_plain_value(getattr(event, "author", None)) if event else None

    for part in content.parts:
        function_call = getattr(part, "function_call", None)
        if not function_call:
            function_response = getattr(part, "function_response", None)
            if not function_response:
                continue

            response_payload = (
                _to_plain_value(getattr(function_response, "response", None))
                or _to_plain_value(getattr(function_response, "output", None))
                or _to_plain_value(getattr(function_response, "result", None))
                or _to_plain_value(getattr(function_response, "content", None))
            )
            if response_payload is not None:
                calls.extend(_extract_nested_tool_calls(response_payload, fallback_agent=author))
            continue

        tool_name = _to_plain_value(getattr(function_call, "name", None))
        tool_args = _to_plain_value(
            getattr(function_call, "args", None) or getattr(function_call, "arguments", None)
        )

        if not tool_name:
            continue

        payload: dict[str, object] = {"tool": tool_name}
        if author:
            payload["agent"] = author
        if tool_args is not None:
            payload["args"] = tool_args

        calls.append(payload)

    return calls


def _append_unique_tool_calls(target: list[dict[str, object]], new_items: list[dict[str, object]]) -> None:
    seen = {
        (str(item.get("agent", "")), str(item.get("tool", "")), str(item.get("args", "")))
        for item in target
    }

    for item in new_items:
        signature = (
            str(item.get("agent", "")),
            str(item.get("tool", "")),
            str(item.get("args", "")),
        )
        if signature in seen:
            continue
        target.append(item)
        seen.add(signature)


def _collect_unique_tool_calls(
    target: list[dict[str, object]], new_items: list[dict[str, object]]
) -> list[dict[str, object]]:
    seen = {
        (str(item.get("agent", "")), str(item.get("tool", "")), str(item.get("args", "")))
        for item in target
    }
    added: list[dict[str, object]] = []

    for item in new_items:
        signature = (
            str(item.get("agent", "")),
            str(item.get("tool", "")),
            str(item.get("args", "")),
        )
        if signature in seen:
            continue
        target.append(item)
        added.append(item)
        seen.add(signature)

    return added


def _extract_delta(full_text: str, previous_text: str) -> str:
    if not full_text:
        return ""
    if not previous_text:
        return full_text
    if full_text.startswith(previous_text):
        return full_text[len(previous_text) :]
    if full_text == previous_text:
        return ""
    return full_text


def _stringify_tool_output(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False).strip()
        except TypeError:
            return str(value).strip()
    return str(value).strip()


def _split_stream_text(text: str, chunk_size: int = _STREAM_CHUNK_SIZE) -> list[str]:
    if not text:
        return []

    cleaned = text
    if len(cleaned) <= chunk_size:
        return [cleaned]

    chunks: list[str] = []
    cursor = 0
    text_len = len(cleaned)
    while cursor < text_len:
        end = min(cursor + chunk_size, text_len)
        if end < text_len:
            split_at = cleaned.rfind(" ", cursor, end)
            if split_at > cursor + (chunk_size // 3):
                end = split_at + 1
        piece = cleaned[cursor:end]
        if piece:
            chunks.append(piece)
        cursor = end

    return chunks


def _extract_tool_output_events(content: object, event: object | None = None) -> list[dict[str, str]]:
    if not content or not getattr(content, "parts", None):
        return []

    author = str(_to_plain_value(getattr(event, "author", None)) or "") if event else ""

    outputs: list[dict[str, str]] = []
    for part in content.parts:
        function_response = getattr(part, "function_response", None)
        if not function_response:
            continue

        tool_name = str(_to_plain_value(getattr(function_response, "name", None)) or "tool")
        raw_output = (
            _to_plain_value(getattr(function_response, "response", None))
            or _to_plain_value(getattr(function_response, "output", None))
            or _to_plain_value(getattr(function_response, "result", None))
            or _to_plain_value(getattr(function_response, "content", None))
        )
        text_output = _stringify_tool_output(raw_output)
        if not text_output:
            continue

        outputs.append(
            {
                "agent": author,
                "tool": tool_name,
                "output": text_output,
            }
        )

    return outputs


def _try_parse_json_like(value: object) -> object:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return value

    text = value.strip()
    if not text:
        return value

    if text[0] not in ("{", "["):
        return value

    try:
        return json.loads(text)
    except Exception:
        return value


def _coerce_score(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except Exception:
            return None
    return None


def _extract_confidence_score(value: object) -> float | None:
    parsed = _try_parse_json_like(value)

    direct_score = _coerce_score(parsed)
    if direct_score is not None:
        return direct_score

    if isinstance(parsed, dict):
        for key in ("confidence", "score"):
            if key in parsed:
                score_value = _extract_confidence_score(parsed.get(key))
                if score_value is not None:
                    return score_value

        for nested in parsed.values():
            score_value = _extract_confidence_score(nested)
            if score_value is not None:
                return score_value

    if isinstance(parsed, list):
        for item in parsed:
            score_value = _extract_confidence_score(item)
            if score_value is not None:
                return score_value

    return None


def _extract_tool_score_events(content: object, event: object | None = None) -> list[dict[str, object]]:
    if not content or not getattr(content, "parts", None):
        return []

    author = str(_to_plain_value(getattr(event, "author", None)) or "") if event else ""
    scores: list[dict[str, object]] = []

    for part in content.parts:
        function_response = getattr(part, "function_response", None)
        if not function_response:
            continue

        tool_name = str(_to_plain_value(getattr(function_response, "name", None)) or "")
        if tool_name.lower() != "intent_router":
            continue

        raw_output = (
            _to_plain_value(getattr(function_response, "response", None))
            or _to_plain_value(getattr(function_response, "output", None))
            or _to_plain_value(getattr(function_response, "result", None))
            or _to_plain_value(getattr(function_response, "content", None))
        )

        score_value = _extract_confidence_score(raw_output)
        if score_value is None:
            continue

        scores.append({
            "agent": author,
            "tool": tool_name,
            "score": score_value,
        })

    return scores


async def _run_once(
    message_text: str,
    session_id: str,
    agent: object,
    response_format: str | None = None,
    trace_context: dict[str, object] | None = None,
) -> dict[str, object]:
    structured_enabled = _structured_output_enabled(response_format)

    if isinstance(trace_context, dict):
        trace_context["session_id"] = session_id

    session_service = InMemorySessionService()
    await session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session_id,
    )

    runner = Runner(
        agent=agent,
        app_name=APP_NAME,
        session_service=session_service,
    )
    message = types.Content(role="user", parts=[types.Part(text=message_text)])

    last_text = ""
    tool_calls: list[dict[str, object]] = []
    tool_observations: list[str] = []
    seen_observations: set[str] = set()
    async for event in runner.run_async(
        user_id=USER_ID,
        session_id=session_id,
        new_message=message,
    ):
        event_tool_calls = _extract_tool_calls(event.content, event)
        _append_unique_tool_calls(tool_calls, event_tool_calls)
        _record_trace_event(
            trace_context,
            event=event,
            event_tool_calls=event_tool_calls,
        )

        if structured_enabled:
            output_events = _extract_tool_output_events(event.content, event)
            for output_event in output_events:
                observation = _format_tool_observation(output_event)
                if observation in seen_observations:
                    continue
                seen_observations.add(observation)
                tool_observations.append(observation)

        current_text = _extract_text(event.content)
        if current_text:
            last_text = current_text

        if event.is_final_response():
            final_text = _extract_text(event.content)
            selected_text = final_text or last_text
            structured_payload = (
                _build_structured_payload(selected_text, tool_observations)
                if structured_enabled
                else None
            )
            response_text = _extract_response_text(selected_text, structured_payload)

            payload: dict[str, object] = {
                "response": response_text,
                "tool_calls": tool_calls,
            }
            if structured_payload:
                payload["structured"] = structured_payload
                payload["api_version"] = "v2"

            if final_text:
                return payload

    structured_payload = (
        _build_structured_payload(last_text, tool_observations)
        if structured_enabled
        else None
    )
    response_text = _extract_response_text(last_text, structured_payload)

    payload = {
        "response": response_text,
        "tool_calls": tool_calls,
    }
    if structured_payload:
        payload["structured"] = structured_payload
        payload["api_version"] = "v2"

    return payload


async def stream_agent(question: str, agent: object):
    """Async generator that yields text chunks from agent events."""
    prompt_text = _build_prompt_with_precomputed_context(question)
    request_id = uuid.uuid4().hex

    session_service = InMemorySessionService()
    session_id = f"stream-{request_id}"
    await session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session_id,
    )

    runner = Runner(
        agent=agent,
        app_name=APP_NAME,
        session_service=session_service,
    )
    message = types.Content(role="user", parts=[types.Part(text=prompt_text)])

    async for event in runner.run_async(
        user_id=USER_ID,
        session_id=session_id,
        new_message=message,
    ):
        text = _extract_text(event.content)
        if text:
            yield text


async def run_agent_streaming(
    question: str,
    agent: object,
    response_format: str | None = None,
    athlete_id: str | int | None = None,
    conversation_question: str | None = None,
):
    """Async generator that yields payload chunks with response/tool_calls."""
    prompt_text = _build_prompt_with_precomputed_context(question)
    request_id = uuid.uuid4().hex
    session1_id = f"session1-{request_id}"
    session2_id = f"session2-{request_id}"
    structured_enabled = _structured_output_enabled(response_format)
    log_question = (
        conversation_question
        if isinstance(conversation_question, str) and conversation_question.strip()
        else question
    )

    async def _stream_attempt(
        message_text: str,
        session_id: str,
        trace_context: dict[str, object] | None = None,
    ):
        if isinstance(trace_context, dict):
            trace_context["session_id"] = session_id

        session_service = InMemorySessionService()
        await session_service.create_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=session_id,
        )

        runner = Runner(
            agent=agent,
            app_name=APP_NAME,
            session_service=session_service,
        )
        message = types.Content(role="user", parts=[types.Part(text=message_text)])

        last_visible_text = ""
        tool_calls: list[dict[str, object]] = []
        emitted_section_counts: dict[str, int] = {}
        tool_observations: list[str] = []
        seen_observations: set[str] = set()
        async for event in runner.run_async(
            user_id=USER_ID,
            session_id=session_id,
            new_message=message,
        ):
            payload: dict[str, object] = {}
            structured_blocks: list[dict[str, object]] = []

            event_tool_calls = _extract_tool_calls(event.content, event)
            added_calls = _collect_unique_tool_calls(
                tool_calls,
                event_tool_calls,
            )
            _record_trace_event(
                trace_context,
                event=event,
                event_tool_calls=event_tool_calls,
            )
            if added_calls:
                payload["tool_calls"] = added_calls

            score_events = _extract_tool_score_events(event.content, event)
            if score_events:
                payload["score"] = score_events[-1].get("score")

            if structured_enabled:
                output_events = _extract_tool_output_events(event.content, event)
                for output_event in output_events:
                    observation_text = _format_tool_observation(output_event)
                    if observation_text in seen_observations:
                        continue
                    seen_observations.add(observation_text)
                    tool_observations.append(observation_text)

                    observation_index = emitted_section_counts.get("observation", 0)
                    emitted_section_counts["observation"] = observation_index + 1
                    structured_blocks.append(
                        {
                            "section": "observation",
                            "text": observation_text,
                            "index": observation_index,
                        }
                    )

            full_text = _extract_text(event.content)

            if full_text:
                if structured_enabled:
                    extracted_sections = _extract_plan_react_sections(full_text)
                    structured_blocks.extend(
                        _collect_new_section_blocks(extracted_sections, emitted_section_counts)
                    )
                    structured_payload = _build_structured_payload(full_text, tool_observations)
                    visible_text = _extract_response_text(full_text, structured_payload)
                else:
                    visible_text = full_text

                visible_delta = _extract_delta(visible_text, last_visible_text)
                if visible_delta:
                    last_visible_text = visible_text
                    payload["response"] = visible_delta

            if structured_blocks:
                payload["structured"] = {
                    "format": "plan_react_v1",
                    "blocks": structured_blocks,
                }

            if not payload:
                continue

            structured_payload = payload.get("structured")
            if isinstance(structured_payload, dict):
                blocks = structured_payload.get("blocks")
                if isinstance(blocks, list):
                    for block_index, block in enumerate(blocks):
                        section_name = str(block.get("section", "")).strip().lower()
                        if section_name not in _PLAN_REACT_SECTIONS_ORDER:
                            section_name = "observation"

                        block_text = str(block.get("text", "")).strip()
                        if not block_text:
                            continue

                        phase_payload: dict[str, object] = {
                            "_event": section_name,
                            "structured": {
                                "format": "plan_react_v1",
                                "section": section_name,
                                "text": block_text,
                                "index": block.get("index", block_index),
                            },
                        }

                        if block_index == 0 and payload.get("tool_calls"):
                            phase_payload["tool_calls"] = payload["tool_calls"]
                        if block_index == 0 and payload.get("score") is not None:
                            phase_payload["score"] = payload["score"]
                        if section_name == "final_answer" and not payload.get("response"):
                            phase_payload["response"] = block_text

                        yield phase_payload

            response_text = str(payload.get("response", ""))
            if not response_text:
                if payload.get("tool_calls") or payload.get("score") is not None:
                    passthrough_payload = {
                        key: value
                        for key, value in payload.items()
                        if key not in {"structured"}
                    }
                    if passthrough_payload:
                        yield passthrough_payload
                continue

            response_chunks = _split_stream_text(response_text)
            for index, response_chunk in enumerate(response_chunks):
                chunk_payload: dict[str, object] = {"response": response_chunk}
                if index == 0 and payload.get("tool_calls"):
                    chunk_payload["tool_calls"] = payload["tool_calls"]
                if index == 0 and payload.get("score") is not None:
                    chunk_payload["score"] = payload["score"]
                yield chunk_payload

        if isinstance(trace_context, dict):
            trace_context["tool_calls"] = list(tool_calls)

    first_trace_context = _create_attempt_trace_context(session1_id)
    first_response_chunks: list[str] = []
    attempts: list[dict[str, object]] = []

    got_response = False
    async for payload in _stream_attempt(prompt_text, session1_id, first_trace_context):
        response_piece = str(payload.get("response", ""))
        if response_piece:
            first_response_chunks.append(response_piece)

        if response_piece.strip() or payload.get("structured"):
            got_response = True
        yield payload

    first_response_text = "".join(first_response_chunks).strip()
    first_tool_calls = first_trace_context.get("tool_calls")
    attempts.append(
        _build_attempt_log(
            attempt=1,
            retry=False,
            trace_context=first_trace_context,
            tool_calls=first_tool_calls if isinstance(first_tool_calls, list) else [],
            response_text=first_response_text,
            succeeded=got_response,
        )
    )

    if got_response:
        await _persist_conversation_log(
            athlete_id=athlete_id,
            payload=_build_conversation_log(
                conversation_id=request_id,
                question=log_question,
                response_text=first_response_text,
                response_format=response_format,
                stream=True,
                attempts=attempts,
                success=True,
            ),
        )
        return

    retry_prompt = (
        f"{prompt_text}\n\n"
        "[Retry obligatorio]\n"
        "La ejecucion anterior no devolvio texto final. Reintenta coordinando a todos los agentes necesarios. "
        "Debes producir una respuesta final en texto para el usuario, usando answer_agent. "
        "Solo pregunta si falta un recurso externo (por ejemplo API key)."
    )

    second_trace_context = _create_attempt_trace_context(session2_id)
    retry_response_chunks: list[str] = []
    retry_got_response = False
    async for payload in _stream_attempt(retry_prompt, session2_id, second_trace_context):
        response_piece = str(payload.get("response", ""))
        if response_piece:
            retry_response_chunks.append(response_piece)

        if response_piece.strip() or payload.get("structured"):
            retry_got_response = True
        yield payload

    retry_response_text = "".join(retry_response_chunks).strip()
    second_tool_calls = second_trace_context.get("tool_calls")
    attempts.append(
        _build_attempt_log(
            attempt=2,
            retry=True,
            trace_context=second_trace_context,
            tool_calls=second_tool_calls if isinstance(second_tool_calls, list) else [],
            response_text=retry_response_text,
            succeeded=retry_got_response,
        )
    )

    if retry_got_response:
        await _persist_conversation_log(
            athlete_id=athlete_id,
            payload=_build_conversation_log(
                conversation_id=request_id,
                question=log_question,
                response_text=retry_response_text,
                response_format=response_format,
                stream=True,
                attempts=attempts,
                success=True,
            ),
        )
        return

    fallback_response = "No se pudo producir una respuesta final en texto. Reintenta la consulta o proporciona mas contexto."
    await _persist_conversation_log(
        athlete_id=athlete_id,
        payload=_build_conversation_log(
            conversation_id=request_id,
            question=log_question,
            response_text=fallback_response,
            response_format=response_format,
            stream=True,
            attempts=attempts,
            success=False,
        ),
    )
    yield {
        "response": fallback_response,
        "tool_calls": [],
    }


async def run_agent(
    question: str,
    agent: object,
    response_format: str | None = None,
    athlete_id: str | int | None = None,
    conversation_question: str | None = None,
) -> dict[str, object]:
    prompt_text = _build_prompt_with_precomputed_context(question)
    request_id = uuid.uuid4().hex
    session1_id = f"session1-{request_id}"
    session2_id = f"session2-{request_id}"
    log_question = (
        conversation_question
        if isinstance(conversation_question, str) and conversation_question.strip()
        else question
    )
    attempts: list[dict[str, object]] = []

    first_trace_context = _create_attempt_trace_context(session1_id)

    first_attempt = await _run_once(
        prompt_text,
        session1_id,
        agent,
        response_format=response_format,
        trace_context=first_trace_context,
    )
    first_response = str(first_attempt.get("response", "")).strip()
    first_tool_calls = first_attempt.get("tool_calls")
    attempts.append(
        _build_attempt_log(
            attempt=1,
            retry=False,
            trace_context=first_trace_context,
            tool_calls=first_tool_calls if isinstance(first_tool_calls, list) else [],
            response_text=first_response,
            succeeded=bool(first_response),
        )
    )

    if first_response:
        await _persist_conversation_log(
            athlete_id=athlete_id,
            payload=_build_conversation_log(
                conversation_id=request_id,
                question=log_question,
                response_text=first_response,
                response_format=response_format,
                stream=False,
                attempts=attempts,
                success=True,
            ),
        )
        return first_attempt

    retry_prompt = (
        f"{prompt_text}\n\n"
        "[Retry obligatorio]\n"
        "La ejecucion anterior no devolvio texto final. Reintenta coordinando a todos los agentes necesarios. "
        "Debes producir una respuesta final en texto para el usuario, usando answer_agent. "
        "Solo pregunta si falta un recurso externo (por ejemplo API key)."
    )

    second_trace_context = _create_attempt_trace_context(session2_id)
    second_attempt = await _run_once(
        retry_prompt,
        session2_id,
        agent,
        response_format=response_format,
        trace_context=second_trace_context,
    )
    second_response = str(second_attempt.get("response", "")).strip()
    second_tool_calls = second_attempt.get("tool_calls")
    attempts.append(
        _build_attempt_log(
            attempt=2,
            retry=True,
            trace_context=second_trace_context,
            tool_calls=second_tool_calls if isinstance(second_tool_calls, list) else [],
            response_text=second_response,
            succeeded=bool(second_response),
        )
    )

    if second_response:
        await _persist_conversation_log(
            athlete_id=athlete_id,
            payload=_build_conversation_log(
                conversation_id=request_id,
                question=log_question,
                response_text=second_response,
                response_format=response_format,
                stream=False,
                attempts=attempts,
                success=True,
            ),
        )
        return second_attempt

    fallback_payload = {
        "response": "No se pudo producir una respuesta final en texto. Reintenta la consulta o proporciona mas contexto.",
        "tool_calls": [],
    }
    await _persist_conversation_log(
        athlete_id=athlete_id,
        payload=_build_conversation_log(
            conversation_id=request_id,
            question=log_question,
            response_text=str(fallback_payload.get("response") or ""),
            response_format=response_format,
            stream=False,
            attempts=attempts,
            success=False,
        ),
    )
    return fallback_payload