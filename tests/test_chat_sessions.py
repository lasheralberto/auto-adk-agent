from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_firestore_client():
    with patch("strava_agent_sdk.services.chat_sessions.firebase_admin") as mock_admin, \
         patch("strava_agent_sdk.services.chat_sessions.firebase_firestore") as mock_fs:
        mock_admin._apps = {"default": MagicMock()}
        client = MagicMock()
        mock_fs.client.return_value = client
        yield client


def test_list_sessions_returns_ordered_list(mock_firestore_client):
    from strava_agent_sdk.services.chat_sessions import ChatSessionsService

    doc1 = MagicMock()
    doc1.to_dict.return_value = {
        "sessionId": "s1", "title": "Hola", "createdAt": "2026-04-21T10:00:00+00:00",
        "updatedAt": "2026-04-21T10:05:00+00:00"
    }
    doc2 = MagicMock()
    doc2.to_dict.return_value = {
        "sessionId": "s2", "title": "Ritmo", "createdAt": "2026-04-20T09:00:00+00:00",
        "updatedAt": "2026-04-20T09:10:00+00:00"
    }

    sessions_ref = MagicMock()
    sessions_ref.order_by.return_value.limit.return_value.stream.return_value = [doc1, doc2]
    mock_firestore_client.collection.return_value.document.return_value.collection.return_value = sessions_ref

    service = ChatSessionsService()
    result = service.list_sessions(athlete_id=123)

    assert len(result["sessions"]) == 2
    assert result["sessions"][0]["sessionId"] == "s1"


def test_create_session_sets_document(mock_firestore_client):
    from strava_agent_sdk.services.chat_sessions import ChatSessionsService

    service = ChatSessionsService()
    result = service.create_session(athlete_id=123, session_id="abc", title="Hola mundo")

    assert result["sessionId"] == "abc"
    assert result["title"] == "Hola mundo"
    assert "createdAt" in result


def test_add_message_writes_document(mock_firestore_client):
    from strava_agent_sdk.services.chat_sessions import ChatSessionsService

    service = ChatSessionsService()
    result = service.add_message(
        athlete_id=123,
        session_id="abc",
        message_id="msg1",
        role="user",
        content="¿Cuál fue mi ritmo?",
        tag="Consulta",
    )

    assert result["messageId"] == "msg1"
    assert "createdAt" in result


def test_delete_session_batch_deletes(mock_firestore_client):
    from strava_agent_sdk.services.chat_sessions import ChatSessionsService

    msg_doc = MagicMock()
    msgs_ref = MagicMock()
    msgs_ref.stream.return_value = [msg_doc]

    session_ref = MagicMock()
    session_ref.collection.return_value = msgs_ref
    mock_firestore_client.collection.return_value.document.return_value.collection.return_value.document.return_value = session_ref

    batch = MagicMock()
    mock_firestore_client.batch.return_value = batch

    service = ChatSessionsService()
    result = service.delete_session(athlete_id=123, session_id="abc")

    assert result["deleted"] is True
