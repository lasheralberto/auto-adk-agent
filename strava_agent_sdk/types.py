from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

JsonDict = dict[str, Any]


@dataclass(slots=True)
class ChatResponse:
    response: str
    tool_calls: list[JsonDict] = field(default_factory=list)
    structured: JsonDict | None = None
    api_version: str | None = None
    retrieval_hits: list[JsonDict] = field(default_factory=list)
    query_mode: str | None = None

    @classmethod
    def from_payload(cls, payload: JsonDict) -> "ChatResponse":
        return cls(
            response=str(payload.get("response") or ""),
            tool_calls=list(payload.get("tool_calls") or []),
            structured=payload.get("structured") if isinstance(payload.get("structured"), dict) else None,
            api_version=str(payload.get("api_version")) if payload.get("api_version") else None,
            retrieval_hits=list(payload.get("retrieval_hits") or []),
            query_mode=str(payload.get("query_mode")) if payload.get("query_mode") else None,
        )

    def to_payload(self) -> JsonDict:
        payload: JsonDict = {
            "response": self.response,
            "tool_calls": self.tool_calls,
            "retrieval_hits": self.retrieval_hits,
        }
        if self.structured is not None:
            payload["structured"] = self.structured
        if self.api_version:
            payload["api_version"] = self.api_version
        if self.query_mode:
            payload["query_mode"] = self.query_mode
        return payload
