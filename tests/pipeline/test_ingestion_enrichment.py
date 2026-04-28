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


from agent.tools.pipeline.workflow import _build_activity_firestore_payload


def test_build_activity_firestore_payload_includes_new_scalar_fields():
    activity = {
        "id": 200,
        "name": "Evening Run",
        "sport_type": "Run",
        "distance": 10000.0,
        "moving_time": 3000,
        "elapsed_time": 3100,
        "calories": 620,
        "suffer_score": 45,
        "weighted_average_watts": 0,
        "max_watts": 0,
        "average_cadence": 82.3,
        "description": "Tempo effort",
        "location_city": "Seville",
        "location_state": "Andalusia",
        "location_country": "Spain",
        "start_latlng": [37.3891, -5.9845],
        "end_latlng": [37.3920, -5.9800],
        "total_photo_count": 0,
    }
    payload = _build_activity_firestore_payload(activity, athlete_id=7)

    assert payload["calories"] == 620
    assert payload["suffer_score"] == 45
    assert payload["average_cadence"] == 82.3
    assert payload["description"] == "Tempo effort"
    assert payload["location_city"] == "Seville"
    assert payload["location_state"] == "Andalusia"
    assert payload["location_country"] == "Spain"
    assert payload["start_latlng"] == [37.3891, -5.9845]
    assert payload["end_latlng"] == [37.3920, -5.9800]
    assert payload["total_photo_count"] == 0


def test_build_activity_firestore_payload_includes_structured_arrays():
    activity = {
        "id": 201,
        "splits_metric": [{"distance": 1000.0, "elapsed_time": 300, "average_heartrate": 155}],
        "segment_efforts": [{"name": "Col du Tourmalet", "elapsed_time": 7200, "pr_rank": 1}],
        "laps": [{"index": 1, "elapsed_time": 1800, "average_watts": 220}],
    }
    payload = _build_activity_firestore_payload(activity, athlete_id=7)

    assert payload["splits_metric"] == activity["splits_metric"]
    assert payload["segment_efforts"] == activity["segment_efforts"]
    assert payload["laps"] == activity["laps"]


def test_build_activity_firestore_payload_omits_missing_new_fields():
    """Fields absent from activity dict must not appear in payload."""
    activity = {"id": 202, "name": "Easy Ride", "sport_type": "Ride"}
    payload = _build_activity_firestore_payload(activity, athlete_id=7)

    for field in ("calories", "suffer_score", "weighted_average_watts", "max_watts",
                  "average_cadence", "description", "location_city", "location_state",
                  "location_country", "start_latlng", "end_latlng", "total_photo_count",
                  "splits_metric", "segment_efforts", "laps"):
        assert field not in payload, f"Unexpected field: {field}"
