from __future__ import annotations

from typing import Any

import requests

from ..storage_backend import AthleteStateStore
from .base import DataConnector

_STRAVA_API_BASE_URL = "https://www.strava.com/api/v3"
_MAX_SYNC_PAGES = 10
_PER_PAGE = 100


def _strava_get(access_token: str, path: str, params: dict[str, Any] | None = None) -> Any:
    response = requests.get(
        url=f"{_STRAVA_API_BASE_URL}{path}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        params={key: value for key, value in (params or {}).items() if value is not None},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


class StravaConnector(DataConnector):
    """Conector para la API de Strava.

    Encapsula autenticación OAuth y las llamadas HTTP a la API de Strava.
    El workflow solo interactúa con la interfaz ``DataConnector``.
    """

    def __init__(self, state_store: AthleteStateStore) -> None:
        self._state_store = state_store

    @property
    def connector_name(self) -> str:
        return "strava"

    def list_syncable_athletes(self) -> list[dict[str, Any]]:
        return self._state_store.list_athletes_with_tokens()

    def get_athlete_profile(self, athlete_id: int) -> dict[str, Any] | None:
        athlete = self._state_store.get_athlete(athlete_id)
        if not athlete:
            return None
        access_token = str(athlete.get("access_token") or "").strip()
        if not access_token:
            return None
        try:
            profile = _strava_get(access_token, "/athlete")
            return profile if isinstance(profile, dict) else None
        except requests.RequestException:
            return None

    def fetch_activities(
        self,
        athlete_id: int,
        after_epoch: int,
        max_pages: int = _MAX_SYNC_PAGES,
        per_page: int = _PER_PAGE,
    ) -> list[dict[str, Any]]:
        athlete = self._state_store.get_athlete(athlete_id)
        if not athlete:
            return []
        access_token = str(athlete.get("access_token") or "").strip()
        if not access_token:
            return []

        activities: list[dict[str, Any]] = []
        capped_per_page = max(1, min(int(per_page), 200))

        for page in range(1, max(1, int(max_pages)) + 1):
            payload = _strava_get(
                access_token,
                "/athlete/activities",
                params={"after": after_epoch, "page": page, "per_page": capped_per_page},
            )
            if not isinstance(payload, list) or not payload:
                break
            activities.extend(a for a in payload if isinstance(a, dict))
            if len(payload) < capped_per_page:
                break

        return activities
