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
