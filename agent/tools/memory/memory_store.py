from datetime import datetime, timezone
from typing import Any

from agent.tools.memory.factory import build_memory_provider
from agent.config.envars import get_setting


DEFAULT_TOP_K = 3
DEFAULT_SUMMARY_MAX_CHARS = 700


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _clip(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _normalize_memory_item(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()

    if isinstance(item, dict):
        question = _to_text(item.get("question"))
        answer_summary = _to_text(item.get("answer_summary"))
        if question and answer_summary:
            return f"Question: {question}\nAnswer Summary: {answer_summary}"
        return _to_text(item)

    return _to_text(item)


def retrieve_similar_memories(question: str, top_k: int | None = None) -> list[str]:
    query = _to_text(question)
    if not query:
        return []

    if top_k is None:
        top_k_raw = _to_text(get_setting("MEMORY_TOP_K", str(DEFAULT_TOP_K)))
        top_k = int(top_k_raw) if top_k_raw.isdigit() else DEFAULT_TOP_K

    try:
        provider = build_memory_provider()
        results = provider.search(query, top_k=top_k)
    except Exception:
        return []

    memories: list[str] = []
    for item in results or []:
        text = _normalize_memory_item(item)
        if text:
            memories.append(text)
    return memories


def save_memory(question: str, final_answer: str) -> bool:
    question_text = _to_text(question)
    answer_text = _to_text(final_answer)
    if not question_text or not answer_text:
        return False

    payload = {
        "question": question_text,
        "answer_summary": _clip(answer_text, DEFAULT_SUMMARY_MAX_CHARS),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        provider = build_memory_provider()
        provider.set(question_text, payload)
    except Exception:
        return False

    return True
