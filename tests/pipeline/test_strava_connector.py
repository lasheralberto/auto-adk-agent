from __future__ import annotations

from agent.tools.pipeline.connectors.base import DataConnector
from typing import Any


class _MinimalConnector(DataConnector):
    """Minimal concrete subclass — does not override fetch_activity_detail."""

    @property
    def connector_name(self) -> str:
        return "minimal"

    def list_syncable_athletes(self) -> list[dict[str, Any]]:
        return []

    def get_athlete_profile(self, athlete_id: int) -> dict[str, Any] | None:
        return None

    def fetch_activities(
        self,
        athlete_id: int,
        after_epoch: int,
        max_pages: int = 10,
        per_page: int = 100,
    ) -> list[dict[str, Any]]:
        return []


def test_base_connector_fetch_activity_detail_returns_none_by_default():
    connector = _MinimalConnector()
    result = connector.fetch_activity_detail(athlete_id=1, activity_id=999)
    assert result is None


from unittest.mock import MagicMock, patch
import requests

from agent.tools.pipeline.connectors.strava import StravaConnector


def _make_connector(athlete_record: dict | None = None) -> StravaConnector:
    state_store = MagicMock()
    state_store.get_athlete.return_value = athlete_record
    return StravaConnector(state_store)


def test_fetch_activity_detail_returns_detail_dict():
    connector = _make_connector({"access_token": "tok123"})
    fake_detail = {
        "id": 42,
        "calories": 500,
        "suffer_score": 77,
        "weighted_average_watts": 210,
        "max_watts": 380,
        "average_cadence": 88.5,
        "description": "Morning ride",
        "location_city": "Madrid",
        "location_state": "Community of Madrid",
        "location_country": "Spain",
        "start_latlng": [40.4168, -3.7038],
        "end_latlng": [40.4200, -3.7010],
        "total_photo_count": 2,
        "splits_metric": [{"distance": 1000.0, "elapsed_time": 210}],
        "segment_efforts": [{"name": "Climb X", "elapsed_time": 120, "kom_rank": None, "pr_rank": 1}],
        "laps": [{"index": 1, "elapsed_time": 3600, "average_watts": 210}],
    }
    with patch("agent.tools.pipeline.connectors.strava._strava_get", return_value=fake_detail) as mock_get:
        result = connector.fetch_activity_detail(athlete_id=1, activity_id=42)

    mock_get.assert_called_once_with("tok123", "/activities/42", params={"include_all_efforts": True})
    assert result == fake_detail


def test_fetch_activity_detail_returns_none_when_no_athlete():
    connector = _make_connector(athlete_record=None)
    result = connector.fetch_activity_detail(athlete_id=1, activity_id=42)
    assert result is None


def test_fetch_activity_detail_returns_none_when_no_token():
    connector = _make_connector({"access_token": ""})
    result = connector.fetch_activity_detail(athlete_id=1, activity_id=42)
    assert result is None


def test_fetch_activity_detail_returns_none_on_request_error():
    connector = _make_connector({"access_token": "tok123"})
    with patch(
        "agent.tools.pipeline.connectors.strava._strava_get",
        side_effect=requests.RequestException("timeout"),
    ):
        result = connector.fetch_activity_detail(athlete_id=1, activity_id=42)
    assert result is None


def test_fetch_activity_detail_returns_none_if_response_is_not_dict():
    connector = _make_connector({"access_token": "tok123"})
    with patch("agent.tools.pipeline.connectors.strava._strava_get", return_value=["bad"]):
        result = connector.fetch_activity_detail(athlete_id=1, activity_id=42)
    assert result is None
