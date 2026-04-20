import os
import pathlib

from google.adk.skills import load_skill_from_dir
from google.adk.models import Gemini
from agent.config.envars import get_secret, get_setting, load_environment_from_sources

load_environment_from_sources()

_SKILLS_DIR = pathlib.Path(__file__).parent.parent / "skills"

# ─── Centralized model configuration ────────────────────────────────────────
# Todos los modelos usados por el sistema se configuran aquí. Cambiar estos
# valores (o las variables de entorno correspondientes) es suficiente para
# propagar el cambio a todas las etapas del pipeline y a los agentes de chat.

# Modelo para el agente principal (chat, planner, sub-agentes ADK).
# En chat, el modelo llega por payload desde el frontend — este valor actúa
# solo como fallback cuando no se recibe model_name en la request.
AGENT_LLM_MODEL: str = (
    get_setting("AGENT_LLM_MODEL", "")
).strip()

# Modelo para el pipeline de wiki (triage, bootstrap, update por página,
# regenerado de _index). Se invoca vía litellm.completion().
# Unificado con AGENT_LLM_MODEL: si no se define por separado, usa el mismo.
WIKI_LLM_MODEL: str = (
    get_setting("WIKI_LLM_MODEL", "") or AGENT_LLM_MODEL
).strip()

# Modelo de embeddings usado para el índice vectorial de la wiki en Pinecone.
WIKI_EMBEDDING_MODEL: str = (
    get_setting("WIKI_EMBEDDING_MODEL", "")  
).strip()

WIKI_EMBEDDING_DIMENSION: int = int(
    (get_setting("WIKI_EMBEDDING_DIMENSION", "") or "1024").strip() or 1024
)


def _configure_vertex_backend() -> None:
    """Configure ADK/GenAI clients to use Vertex AI instead of API key mode."""
    project_id = (get_setting("GOOGLE_CLOUD_PROJECT", "") or "").strip() or "strava-chat"
    location = (get_setting("GOOGLE_CLOUD_LOCATION", "") or "").strip() or "us-central1"
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project_id)
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", location)


_configure_vertex_backend()


def _normalize_litellm_model_name(raw_model: str) -> str:
    normalized = (raw_model or "").strip()
    if not normalized:
        return AGENT_LLM_MODEL

    if "/" in normalized:
        return normalized

    return normalized


def get_wiki_llm_model() -> str:
    """Modelo LLM para el pipeline de wiki, con prefijo gemini/ si aplica."""
    raw = WIKI_LLM_MODEL
    return raw


def get_wiki_embedding_model() -> str:
    return WIKI_EMBEDDING_MODEL


def get_wiki_embedding_dimension() -> int:
    return WIKI_EMBEDDING_DIMENSION


# ─── Load skills ──────────────────────────────────────────────────────────────
answer_agent_skill = load_skill_from_dir(_SKILLS_DIR / "answer-agent")
intent_router_skill = load_skill_from_dir(_SKILLS_DIR / "intent-router")
plan_react_planner_skill = load_skill_from_dir(_SKILLS_DIR / "plan-react-planner")
strava_ingestion_skill = load_skill_from_dir(_SKILLS_DIR / "strava-ingestion-agent")
query_skill = load_skill_from_dir(_SKILLS_DIR / "query-agent")


def get_llm_provider(model_name: str | None = None) -> Gemini:
    google_api_key = (get_secret("GOOGLE_API_KEY", "") or "").strip()
    if google_api_key:
        os.environ["GOOGLE_API_KEY"] = google_api_key

    # El modelo central se define en AGENT_LLM_MODEL. Si el caller pasa un
    # model_name explícito, se respeta; en caso contrario se usa el valor centralizado.
    if model_name:
        selected_model = _normalize_litellm_model_name(str(model_name))
    else:
        selected_model = AGENT_LLM_MODEL
    return Gemini(model=selected_model)
