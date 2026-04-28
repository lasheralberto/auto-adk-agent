from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class DataConnector(ABC):
    """Interfaz abstracta para conectores de obtención de datos de actividad deportiva.

    Cada implementación encapsula la autenticación y el protocolo de comunicación
    con un servicio externo (Strava, Garmin, Polar, etc.). El pipeline de workflow
    opera exclusivamente a través de esta interfaz y desconoce el servicio subyacente.
    """

    @property
    @abstractmethod
    def connector_name(self) -> str:
        """Identificador único del conector (p. ej. 'strava', 'garmin')."""
        ...

    @abstractmethod
    def list_syncable_athletes(self) -> list[dict[str, Any]]:
        """Devuelve los atletas que pueden sincronizarse via este conector.

        Cada elemento debe incluir al menos ``athlete_id`` (int).
        """
        ...

    @abstractmethod
    def get_athlete_profile(self, athlete_id: int) -> dict[str, Any] | None:
        """Obtiene el perfil del atleta desde el servicio externo.

        Devuelve el payload del perfil o ``None`` si no está disponible.
        """
        ...

    @abstractmethod
    def fetch_activities(
        self,
        athlete_id: int,
        after_epoch: int,
        max_pages: int = 10,
        per_page: int = 100,
    ) -> list[dict[str, Any]]:
        """Obtiene las actividades del atleta posteriores a ``after_epoch``.

        Gestiona la paginación internamente y devuelve una lista plana de
        actividades en el formato nativo del servicio.
        """
        ...

    def fetch_activity_detail(self, athlete_id: int, activity_id: int) -> dict[str, Any] | None:
        """Returns full activity detail or None if unsupported by this connector."""
        return None
