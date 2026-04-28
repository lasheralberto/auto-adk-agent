from __future__ import annotations

from unittest.mock import MagicMock, call, patch
from typing import Any

from agent.tools.pipeline.workflow import run_ingestion


def _make_summary_activity(activity_id: int = 100) -> dict[str, Any]:
    return {
        "id": activity_id,
        "name": "Morning Ride",
        "sport_type": "Ride",
        "distance": 30000.0,
        "moving_time": 3600,
        "elapsed_time": 3700,
        "total_elevation_gain": 250.0,
        "start_date": "2026-04-28T07:00:00Z",
        "start_date_local": "2026-04-28T09:00:00+02:00",
    }


def _make_detail_activity(activity_id: int = 100) -> dict[str, Any]:
    return {
        "id": activity_id,
        "calories": 850,
        "suffer_score": 62,
        "weighted_average_watts": 195,
        "max_watts": 420,
        "average_cadence": 87.0,
        "description": "Good legs today",
        "location_city": "Barcelona",
        "location_state": "Catalonia",
        "location_country": "Spain",
        "start_latlng": [41.3851, 2.1734],
        "end_latlng": [41.3900, 2.1800],
        "total_photo_count": 1,
        "splits_metric": [{"distance": 1000.0, "elapsed_time": 120}],
        "segment_efforts": [{"name": "Montjuic", "elapsed_time": 300, "pr_rank": 2}],
        "laps": [{"index": 1, "elapsed_time": 3600, "average_watts": 195}],
    }


def _make_connector(activities: list, detail: dict | None) -> MagicMock:
    connector = MagicMock()
    connector.connector_name = "strava"
    connector.list_syncable_athletes.return_value = [{"athlete_id": 1}]
    connector.fetch_latest_activities.return_value = activities
    connector.fetch_activity_detail.return_value = detail
    connector.get_athlete_profile.return_value = {}
    return connector


def _make_stores(existing_ids: set[str] | None = None):
    state_store = MagicMock()
    state_store.list_athletes_with_tokens.return_value = [{"athlete_id": 1}]
    state_store.get_athlete.return_value = {}
    state_store.update_sync_state.return_value = None

    artifact_store = MagicMock()
    artifact_store.mode = "local"
    existing = list(existing_ids or set())
    artifact_store.list_paths.return_value = [f"pipeline/research-wiki-input/{id_}" for id_ in existing]
    return state_store, artifact_store


def test_run_ingestion_merges_detail_into_blob():
    """Detail fields must appear in the JSON written to GCS."""
    summary = _make_summary_activity(100)
    detail = _make_detail_activity(100)
    connector = _make_connector([summary], detail)
    state_store, artifact_store = _make_stores()

    import json

    with (
        patch("agent.tools.pipeline.workflow.AthleteStateStore", return_value=state_store),
        patch("agent.tools.pipeline.workflow.ArtifactStore", return_value=artifact_store),
    ):
        run_ingestion(connector)

    connector.fetch_activity_detail.assert_called_once_with(1, 100)

    write_calls = artifact_store.write_text.call_args_list
    assert len(write_calls) == 1
    blob_path, blob_content = write_calls[0][0]
    saved = json.loads(blob_content)
    assert saved["calories"] == 850
    assert saved["suffer_score"] == 62
    assert saved["location_city"] == "Barcelona"
    assert saved["segment_efforts"] == [{"name": "Montjuic", "elapsed_time": 300, "pr_rank": 2}]
    assert saved["laps"] == [{"index": 1, "elapsed_time": 3600, "average_watts": 195}]


def test_run_ingestion_falls_back_to_summary_when_detail_is_none():
    """If fetch_activity_detail returns None, activity is still ingested (summary only)."""
    summary = _make_summary_activity(101)
    connector = _make_connector([summary], detail=None)
    state_store, artifact_store = _make_stores()

    import json

    with (
        patch("agent.tools.pipeline.workflow.AthleteStateStore", return_value=state_store),
        patch("agent.tools.pipeline.workflow.ArtifactStore", return_value=artifact_store),
    ):
        run_ingestion(connector)

    write_calls = artifact_store.write_text.call_args_list
    assert len(write_calls) == 1
    _, blob_content = write_calls[0][0]
    saved = json.loads(blob_content)
    assert saved["id"] == 101
    assert "calories" not in saved


def test_run_ingestion_skips_detail_for_existing_activities():
    """fetch_activity_detail must NOT be called for already-ingested activities."""
    summary = _make_summary_activity(102)
    connector = _make_connector([summary], detail=_make_detail_activity(102))
    state_store, artifact_store = _make_stores(existing_ids={"102"})

    with (
        patch("agent.tools.pipeline.workflow.AthleteStateStore", return_value=state_store),
        patch("agent.tools.pipeline.workflow.ArtifactStore", return_value=artifact_store),
    ):
        run_ingestion(connector)

    connector.fetch_activity_detail.assert_not_called()
    artifact_store.write_text.assert_not_called()
