from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import firebase_admin
    from firebase_admin import firestore as firebase_firestore
except Exception:  # noqa: BLE001
    firebase_admin = None
    firebase_firestore = None


WIKI_RESEARCH_CHAT_AGENT_ID = "wiki_research_chat"

MAX_AGENTS = 5

WIKI_CONTEXT_BLOCK = (
    "Se te ha proporcionado un subconjunto de la wiki del atleta con ID %%ATHLETE_ID%%, "
    "seleccionado por relevancia frente a la pregunta del usuario (RAG). "
    "La wiki completa contiene múltiples páginas especializadas (perfil de fitness, "
    "gestión de fatiga, recomendaciones, etc.) que se actualizan automáticamente con "
    "cada nueva actividad.\n\n"
    "WIKI DEL ATLETA (páginas relevantes):\n"
    "### INICIO DE LA WIKI ###\n"
    "%%WIKI%%\n"
    "### FIN DE LA WIKI ###\n\n"
)

DEFAULT_WIKI_RESEARCH_CHAT_TEMPLATE = (
    "Eres un asistente experto en análisis de entrenamiento deportivo para atletas de Strava.\n\n"
    "Instrucciones de respuesta:\n"
    "- Responde siempre en español.\n"
    "- Basa tus respuestas en la información de la wiki proporcionada.\n"
    "- Cuando cites datos, indica de qué página de la wiki provienen.\n"
    "- Si la pregunta no puede responderse con las páginas proporcionadas, "
    "explícalo brevemente y sugiere al usuario reformular la pregunta o "
    "ejecutar una nueva pipeline de investigación para actualizar la wiki.\n"
    "- No inventes datos ni tendencias que no estén respaldadas por las fuentes proporcionadas.\n"
    "- Sé conciso pero completo."
)


DEFAULT_TEMPLATES: dict[str, dict[str, Any]] = {
    WIKI_RESEARCH_CHAT_AGENT_ID: {
        "name": "Wiki Research Chat Agent",
        "description": (
            "Agente que responde preguntas sobre la wiki del atleta."
        ),
        "instruction_template": DEFAULT_WIKI_RESEARCH_CHAT_TEMPLATE,
    },
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentPromptStore:
    """CRUD store for agent prompt templates backed by Firestore.

    Falls back to a local JSON file when Firestore is not available so that
    development workflows keep working without cloud credentials.
    """

    def __init__(self) -> None:
        self._collection = os.environ.get("FIRESTORE_AGENTS_COLLECTION", "agents")
        self._state_path = (
            Path(tempfile.gettempdir()) / "strava_agent_state" / "agent_prompts.json"
        )
        self._state_path.parent.mkdir(parents=True, exist_ok=True)

        self._client: Any | None = None
        use_firestore = os.environ.get("USE_FIRESTORE_STATE", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
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

    @property
    def mode(self) -> str:
        return "firestore" if self._client is not None else "local"

    def _load_local(self) -> dict[str, Any]:
        if not self._state_path.exists():
            return {}
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_local(self, payload: dict[str, Any]) -> None:
        self._state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _default_record(self, agent_id: str) -> dict[str, Any] | None:
        defaults = DEFAULT_TEMPLATES.get(agent_id)
        if not defaults:
            return None
        return {
            "agent_id": agent_id,
            "name": defaults.get("name") or agent_id,
            "description": defaults.get("description") or "",
            "instruction_template": defaults["instruction_template"],
            "is_default": True,
            "updated_at": None,
        }

    def get(self, agent_id: str) -> dict[str, Any] | None:
        """Return the stored record (or defaults) for ``agent_id``."""

        if self._client is not None:
            try:
                doc = self._client.collection(self._collection).document(agent_id).get()
                if doc.exists:
                    payload = doc.to_dict() or {}
                    if isinstance(payload, dict) and payload.get("instruction_template"):
                        defaults = DEFAULT_TEMPLATES.get(agent_id) or {}
                        record = {
                            "agent_id": agent_id,
                            "name": payload.get("name") or defaults.get("name") or agent_id,
                            "description": payload.get("description") or defaults.get("description") or "",
                            "instruction_template": str(payload.get("instruction_template")),
                            "is_default": False,
                            "updated_at": payload.get("updated_at"),
                            "updated_by": payload.get("updated_by"),
                        }
                        return record
            except Exception:  # noqa: BLE001
                self._client = None

        local = self._load_local()
        payload = local.get(agent_id)
        if isinstance(payload, dict) and payload.get("instruction_template"):
            defaults = DEFAULT_TEMPLATES.get(agent_id) or {}
            return {
                "agent_id": agent_id,
                "name": payload.get("name") or defaults.get("name") or agent_id,
                "description": payload.get("description") or defaults.get("description") or "",
                "instruction_template": str(payload.get("instruction_template")),
                "is_default": False,
                "updated_at": payload.get("updated_at"),
                "updated_by": payload.get("updated_by"),
            }

        return self._default_record(agent_id)

    def create(
        self,
        agent_id: str,
        *,
        name: str,
        description: str = "",
        instruction_template: str,
        updated_by: str | None = None,
    ) -> dict[str, Any]:
        """Create a brand-new agent.  Raises if id already exists or limit reached."""
        agent_id = agent_id.strip()
        if not agent_id:
            raise ValueError("agent_id must be a non-empty string.")
        if not isinstance(instruction_template, str) or not instruction_template.strip():
            raise ValueError("instruction_template must be a non-empty string.")

        # Check if already exists
        existing = self.get(agent_id)
        if existing is not None:
            raise ValueError(f"Agent '{agent_id}' already exists.")

        # Enforce max limit
        current_count = len(self.list_all())
        if current_count >= MAX_AGENTS:
            raise ValueError(f"Maximum number of agents ({MAX_AGENTS}) reached.")

        now_iso = _utc_now_iso()
        payload: dict[str, Any] = {
            "agent_id": agent_id,
            "name": name or agent_id,
            "description": description,
            "instruction_template": instruction_template,
            "updated_at": now_iso,
            "updated_by": updated_by,
        }

        if self._client is not None:
            try:
                self._client.collection(self._collection).document(agent_id).set(payload)
                return {**payload, "is_default": False}
            except Exception:  # noqa: BLE001
                self._client = None

        local = self._load_local()
        local[agent_id] = payload
        self._save_local(local)
        return {**payload, "is_default": False}

    def set(
        self,
        agent_id: str,
        instruction_template: str,
        *,
        name: str | None = None,
        description: str | None = None,
        updated_by: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(instruction_template, str) or not instruction_template.strip():
            raise ValueError("instruction_template must be a non-empty string.")

        existing = self.get(agent_id)
        if existing is None:
            raise ValueError(f"Unknown agent_id '{agent_id}'.")

        now_iso = _utc_now_iso()
        payload: dict[str, Any] = {
            "agent_id": agent_id,
            "name": name or existing.get("name") or agent_id,
            "description": description if description is not None else (existing.get("description") or ""),
            "instruction_template": instruction_template,
            "updated_at": now_iso,
            "updated_by": updated_by,
        }

        if self._client is not None:
            try:
                self._client.collection(self._collection).document(agent_id).set(payload, merge=True)
                return {**payload, "is_default": False}
            except Exception:  # noqa: BLE001
                self._client = None

        local = self._load_local()
        local[agent_id] = payload
        self._save_local(local)
        return {**payload, "is_default": False}

    def delete(self, agent_id: str) -> bool:
        """Delete a custom agent. Cannot delete default agents."""
        agent_id = agent_id.strip()
        if agent_id in DEFAULT_TEMPLATES:
            raise ValueError(f"Cannot delete default agent '{agent_id}'.")

        existing = self.get(agent_id)
        if existing is None:
            raise ValueError(f"Agent '{agent_id}' not found.")

        if self._client is not None:
            try:
                self._client.collection(self._collection).document(agent_id).delete()
                return True
            except Exception:  # noqa: BLE001
                self._client = None

        local = self._load_local()
        local.pop(agent_id, None)
        self._save_local(local)
        return True

    def list_all(self) -> list[dict[str, Any]]:
        """Return all agents: defaults merged with Firestore/local custom ones."""
        seen: set[str] = set()
        records: list[dict[str, Any]] = []

        # 1. Default agents (always present)
        for agent_id in DEFAULT_TEMPLATES:
            record = self.get(agent_id)
            if record:
                records.append(record)
                seen.add(agent_id)

        # 2. Custom agents from Firestore
        if self._client is not None:
            try:
                docs = self._client.collection(self._collection).stream()
                for doc in docs:
                    aid = doc.id
                    if aid in seen:
                        continue
                    payload = doc.to_dict() or {}
                    if payload.get("instruction_template"):
                        records.append({
                            "agent_id": aid,
                            "name": payload.get("name") or aid,
                            "description": payload.get("description") or "",
                            "instruction_template": str(payload["instruction_template"]),
                            "is_default": False,
                            "updated_at": payload.get("updated_at"),
                            "updated_by": payload.get("updated_by"),
                        })
                        seen.add(aid)
                return records
            except Exception:  # noqa: BLE001
                self._client = None

        # 3. Fallback: local custom agents
        local = self._load_local()
        for aid, payload in local.items():
            if aid in seen:
                continue
            if isinstance(payload, dict) and payload.get("instruction_template"):
                records.append({
                    "agent_id": aid,
                    "name": payload.get("name") or aid,
                    "description": payload.get("description") or "",
                    "instruction_template": str(payload["instruction_template"]),
                    "is_default": False,
                    "updated_at": payload.get("updated_at"),
                    "updated_by": payload.get("updated_by"),
                })

        return records

    def get_template(self, agent_id: str) -> str:
        record = self.get(agent_id)
        if record and record.get("instruction_template"):
            return str(record["instruction_template"])
        defaults = DEFAULT_TEMPLATES.get(agent_id)
        if defaults:
            return str(defaults["instruction_template"])
        raise ValueError(f"Unknown agent_id '{agent_id}'.")


def render_template(template: str, values: dict[str, str]) -> str:
    rendered = template
    for placeholder, value in values.items():
        rendered = rendered.replace(placeholder, value)
    return rendered
