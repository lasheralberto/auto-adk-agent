import io
import os
from datetime import datetime, timezone
from typing import Any, Optional

from openai import OpenAI
from agent.config.envars import get_secret, get_setting


DEFAULT_VECTOR_STORE_NAME = "orchestrator-memory"


class OpenAIProvider:
    def __init__(
        self,
        api_key: str | None = None,
        vector_store_id: str | None = None,
        expires_after_days: int | None = None,
        summary_max_chars: int = 700,
    ) -> None:
        self._api_key = api_key or get_secret("OPENAI_API_KEY")
        self._vector_store_id = vector_store_id or get_setting("VECTOR_STORE_ID")
        self._expires_after_days = expires_after_days
        self._summary_max_chars = summary_max_chars

    def _client(self) -> OpenAI:
        return OpenAI(api_key=self._api_key)

    def _to_text(self, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def _clip(self, text: str) -> str:
        if len(text) <= self._summary_max_chars:
            return text
        return text[: self._summary_max_chars - 3].rstrip() + "..."

    def _extract_search_text(self, item: Any) -> str:
        chunks: list[str] = []
        for chunk in getattr(item, "content", []) or []:
            text_value = ""
            if isinstance(chunk, dict):
                text_obj = chunk.get("text")
                if isinstance(text_obj, dict):
                    text_value = self._to_text(text_obj.get("value"))
                else:
                    text_value = self._to_text(text_obj)
            else:
                text_obj = getattr(chunk, "text", None)
                text_value = self._to_text(getattr(text_obj, "value", text_obj))

            if text_value:
                chunks.append(text_value)

        if chunks:
            return "\n".join(chunks)
        return self._to_text(getattr(item, "text", ""))

    def _update_vector_store(
        self,
        vector_store_id: str,
        name: str | None = None,
        metadata: dict[str, str] | None = None,
        expires_after_days: int | None = None,
    ) -> Any:
        client = self._client()

        payload: dict[str, Any] = {}
        if name:
            payload["name"] = name
        if metadata:
            payload["metadata"] = metadata
        if isinstance(expires_after_days, int) and expires_after_days > 0:
            payload["expires_after"] = {
                "anchor": "last_active_at",
                "days": expires_after_days,
            }

        if not payload:
            return client.vector_stores.retrieve(vector_store_id=vector_store_id)
        return client.vector_stores.update(vector_store_id=vector_store_id, **payload)

    def _ensure_vector_store(self) -> str:
        client = self._client()
        vector_store_id = self._vector_store_id or get_setting("VECTOR_STORE_ID")

        expires_after_days_raw = str(get_setting("MEMORY_EXPIRY_DAYS", "")).strip()
        env_expiry_days = int(expires_after_days_raw) if expires_after_days_raw.isdigit() else None
        expires_after_days = self._expires_after_days if self._expires_after_days is not None else env_expiry_days

        if not vector_store_id:
            create_payload: dict[str, Any] = {"name": DEFAULT_VECTOR_STORE_NAME}
            if isinstance(expires_after_days, int) and expires_after_days > 0:
                create_payload["expires_after"] = {
                    "anchor": "last_active_at",
                    "days": expires_after_days,
                }
            vector_store = client.vector_stores.create(**create_payload)
            self._vector_store_id = vector_store.id
            os.environ["VECTOR_STORE_ID"] = vector_store.id
            return vector_store.id

        self._update_vector_store(
            vector_store_id=vector_store_id,
            name=DEFAULT_VECTOR_STORE_NAME,
            metadata={
                "source": "orchestrator-memory",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            expires_after_days=expires_after_days,
        )
        self._vector_store_id = vector_store_id
        return vector_store_id

    def get(self, key: str) -> Optional[Any]:
        return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        del ttl
        question_text = self._to_text(key)

        if isinstance(value, dict):
            answer_source = value.get("answer_summary") or value.get("final_answer") or value.get("answer")
            answer_text = self._to_text(answer_source)
        else:
            answer_text = self._to_text(value)

        if not question_text or not answer_text:
            raise ValueError("question and answer are required")

        created_at = datetime.now(timezone.utc).isoformat()
        summary = self._clip(answer_text)
        content = (
            f"Question: {question_text}\n"
            f"Answer Summary: {summary}\n"
            f"Created At: {created_at}"
        )

        memory_file = io.BytesIO(content.encode("utf-8"))
        memory_file.name = f"memory-{int(datetime.now(timezone.utc).timestamp())}.txt"

        store_id = self._ensure_vector_store()
        client = self._client()

        try:
            client.vector_stores.files.upload_and_poll(
                vector_store_id=store_id,
                file=memory_file,
            )
        except Exception:
            uploaded_file = client.files.create(file=memory_file, purpose="assistants")
            client.vector_stores.files.create(
                vector_store_id=store_id,
                file_id=uploaded_file.id,
            )

        try:
            self._update_vector_store(
                vector_store_id=store_id,
                metadata={
                    "source": "orchestrator-memory",
                    "last_memory_at": created_at,
                },
            )
        except Exception:
            pass

    def delete(self, key: str) -> None:
        del key

    def search(self, query: str, top_k: int = 5) -> list[Any]:
        query_text = self._to_text(query)
        if not query_text:
            return []

        safe_top_k = top_k if isinstance(top_k, int) and top_k > 0 else 5
        store_id = self._ensure_vector_store()
        client = self._client()

        results = client.vector_stores.search(
            vector_store_id=store_id,
            query=query_text,
            max_num_results=safe_top_k,
        )

        memories: list[str] = []
        for item in getattr(results, "data", []) or []:
            text = self._extract_search_text(item)
            if text:
                memories.append(text)
        return memories
