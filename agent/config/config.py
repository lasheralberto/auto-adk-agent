import os
import pathlib

from google.adk.skills import load_skill_from_dir
from google.adk.models.lite_llm import LiteLlm
from agent.config.envars import get_secret, get_setting, load_environment_from_sources

load_environment_from_sources()

_SKILLS_DIR = pathlib.Path(__file__).parent.parent / "skills"
_DEFAULT_GEMINI_MODEL = "gemini/gemini-2.5-flash"


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
        return _DEFAULT_GEMINI_MODEL

    if "/" in normalized:
        return normalized

    if normalized.lower().startswith("gemini"):
        return f"gemini/{normalized}"

    return normalized

# ─── Load skills ──────────────────────────────────────────────────────────────
answer_agent_skill = load_skill_from_dir(_SKILLS_DIR / "answer-agent")
intent_router_skill = load_skill_from_dir(_SKILLS_DIR / "intent-router")
plan_react_planner_skill = load_skill_from_dir(_SKILLS_DIR / "plan-react-planner")
strava_ingestion_skill = load_skill_from_dir(_SKILLS_DIR / "strava-ingestion-agent")
query_skill = load_skill_from_dir(_SKILLS_DIR / "query-agent")


def get_llm_provider(model_name: str | None = None) -> LiteLlm:
    google_api_key = (get_secret("GOOGLE_API_KEY", "") or "").strip()
    if google_api_key:
        os.environ["GOOGLE_API_KEY"] = google_api_key

    configured_model = model_name if isinstance(model_name, str) else get_setting("GEMINI_MODEL", "")
    selected_model = _normalize_litellm_model_name(str(configured_model or ""))
    return LiteLlm(model=selected_model)
