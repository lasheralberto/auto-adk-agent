from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

try:
    import firebase_admin
    from firebase_admin import firestore as firebase_firestore
except Exception:  # noqa: BLE001
    firebase_admin = None
    firebase_firestore = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_plain(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:  # noqa: BLE001
            return str(value)
    return value


def _doc_to_plain(data: dict[str, Any]) -> dict[str, Any]:
    return {k: _to_plain(v) for k, v in data.items()}


class ChatSessionsService:
    CHATS_COLLECTION = "chats"

    def __init__(self) -> None:
        self._client: Any | None = None
        use_firestore = os.environ.get("USE_FIRESTORE_STATE", "true").strip().lower() in {
            "1", "true", "yes", "on",
        }
        if use_firestore and firebase_firestore is not None and firebase_admin is not None:
            try:
                if not firebase_admin._apps:
                    project_id = os.environ.get("PROJECT_ID") or None
                    firebase_admin.initialize_app(
                        options={"projectId": project_id} if project_id else None
                    )
                self._client = firebase_firestore.client()
            except Exception:  # noqa: BLE001
                self._client = None

    def _athlete_doc_ref(self, athlete_id: int) -> Any:
        return self._client.collection(self.CHATS_COLLECTION).document(str(athlete_id))

    def _session_ref(self, athlete_id: int, session_id: str) -> Any:
        return self._athlete_doc_ref(athlete_id).collection("sessions").document(session_id)

    def _messages_ref(self, athlete_id: int, session_id: str) -> Any:
        return self._session_ref(athlete_id, session_id).collection("messages")

    # ── Public API ────────────────────────────────────────────────────────────

    def list_sessions(self, *, athlete_id: int) -> dict[str, Any]:
        if self._client is None:
            return {"sessions": []}

        sessions_ref = (
            self._athlete_doc_ref(athlete_id)
            .collection("sessions")
            .order_by("updatedAt", direction=firebase_firestore.Query.DESCENDING)
            .limit(50)
        )
        docs = sessions_ref.stream()
        sessions = []
        for doc in docs:
            data = doc.to_dict() or {}
            sessions.append(_doc_to_plain(data))
        return {"sessions": sessions}

    def create_session(
        self,
        *,
        athlete_id: int,
        session_id: str,
        title: str,
    ) -> dict[str, Any]:
        now = _utc_now_iso()

        if self._client is not None:
            # Upsert parent chats/{athleteId} document
            self._athlete_doc_ref(athlete_id).set(
                {"athleteId": athlete_id, "updatedAt": now},
                merge=True,
            )
            # Set createdAt only on first write
            self._athlete_doc_ref(athlete_id).set(
                {"createdAt": now},
                merge=True,
            )

            session_data: dict[str, Any] = {
                "sessionId": session_id,
                "title": title,
                "createdAt": now,
                "updatedAt": now,
            }
            self._session_ref(athlete_id, session_id).set(session_data)
            return session_data

        return {"sessionId": session_id, "title": title, "createdAt": now, "updatedAt": now}

    def get_messages(self, *, athlete_id: int, session_id: str) -> dict[str, Any]:
        if self._client is None:
            return {"messages": []}

        docs = (
            self._messages_ref(athlete_id, session_id)
            .order_by("createdAt")
            .stream()
        )
        messages = []
        for doc in docs:
            data = doc.to_dict() or {}
            messages.append(_doc_to_plain(data))
        return {"messages": messages}

    def add_message(
        self,
        *,
        athlete_id: int,
        session_id: str,
        message_id: str,
        role: str,
        content: str,
        tag: str,
        structured: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = _utc_now_iso()

        message_data: dict[str, Any] = {
            "messageId": message_id,
            "role": role,
            "content": content,
            "tag": tag,
            "createdAt": now,
        }
        if structured is not None:
            message_data["structured"] = structured

        if self._client is not None:
            self._messages_ref(athlete_id, session_id).document(message_id).set(message_data)
            # Update session + athlete updatedAt
            self._session_ref(athlete_id, session_id).set({"updatedAt": now}, merge=True)
            self._athlete_doc_ref(athlete_id).set({"updatedAt": now}, merge=True)

        return {"messageId": message_id, "createdAt": now}

    def update_session(
        self,
        *,
        athlete_id: int,
        session_id: str,
        title: str | None = None,
    ) -> dict[str, Any]:
        now = _utc_now_iso()
        update: dict[str, Any] = {"updatedAt": now}
        if title is not None:
            update["title"] = title

        if self._client is not None:
            self._session_ref(athlete_id, session_id).set(update, merge=True)

        return {"sessionId": session_id, "updatedAt": now}

    def delete_session(self, *, athlete_id: int, session_id: str) -> dict[str, Any]:
        if self._client is None:
            return {"deleted": True}

        # Batch-delete all messages first
        batch = self._client.batch()
        msg_docs = list(self._messages_ref(athlete_id, session_id).stream())
        for doc in msg_docs:
            batch.delete(doc.reference)
        if msg_docs:
            batch.commit()

        # Delete session document
        self._session_ref(athlete_id, session_id).delete()
        return {"deleted": True}
