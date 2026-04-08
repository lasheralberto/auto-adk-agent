import os
import pathlib

from google.adk.skills import load_skill_from_dir
from google.adk.models.lite_llm import LiteLlm
from agent.config.envars import get_secret, get_setting, load_environment_from_sources

load_environment_from_sources()

_SKILLS_DIR = pathlib.Path(__file__).parent.parent / "skills"


def _configure_vertex_backend() -> None:
    """Configure ADK/GenAI clients to use Vertex AI instead of API key mode."""
    project_id = (get_setting("GOOGLE_CLOUD_PROJECT", "") or "").strip() or "vmo-gemini"
    location = (get_setting("GOOGLE_CLOUD_LOCATION", "") or "").strip() or "us-central1"
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project_id)
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", location)


_configure_vertex_backend()

# ─── Load skills ──────────────────────────────────────────────────────────────
answer_agent_skill = load_skill_from_dir(_SKILLS_DIR / "answer-agent")
intent_router_skill = load_skill_from_dir(_SKILLS_DIR / "intent-router")
plan_react_planner_skill = load_skill_from_dir(_SKILLS_DIR / "plan-react-planner")
strava_ingestion_skill = load_skill_from_dir(_SKILLS_DIR / "strava-ingestion-agent")
activity_analysis_skill = load_skill_from_dir(_SKILLS_DIR / "activity-analysis-agent")
daily_summary_skill = load_skill_from_dir(_SKILLS_DIR / "daily-summary-agent")
performance_insight_skill = load_skill_from_dir(_SKILLS_DIR / "performance-insight-agent")
wiki_builder_skill = load_skill_from_dir(_SKILLS_DIR / "wiki-builder-agent")
embedding_skill = load_skill_from_dir(_SKILLS_DIR / "embedding-agent")
query_skill = load_skill_from_dir(_SKILLS_DIR / "query-agent")


def get_llm_provider(llm_provider: str | None = None, model_name: str | None = None) -> LiteLlm:
    # Prefer the explicit parameter `llm_provider` (used when called from /ask).
    # If it's not provided, fall back to environment variables (for import-time initialization).
    provider_raw = llm_provider if llm_provider else (get_setting("LLM_PROVIDER") or get_setting("LLM") or "")
    if not provider_raw:
        raise ValueError("Unsupported LLM_PROVIDER: None. Set LLM_PROVIDER in .env or pass via request payload.")

    # Support combined format 'provider/model' (e.g. 'openai/gpt-4o')
    provider_parts = provider_raw.split("/", 1)
    provider = provider_parts[0].strip().lower()
    if len(provider_parts) > 1 and not model_name:
        model_name = provider_parts[1].strip()

    if provider == "google":
        api_key = get_secret("GOOGLE_API_KEY", "")
        if api_key:
            os.environ["GOOGLE_API_KEY"] = api_key
        selected_model = (model_name or get_setting("GEMINI_MODEL") or "gemini-2.5-flash").strip()
        model = LiteLlm(model=selected_model)
    elif provider == "openai":
        api_key = get_secret("OPENAI_API_KEY", "")
        if api_key:
            os.environ["OPENAI_API_KEY"] = api_key
        selected_model = (model_name or get_setting("OPENAI_MODEL") or "gpt-3.5-turbo").strip()
        model = LiteLlm(model=selected_model)
    elif provider == "azure":
        # Azure handling could go here if needed; reuse model_name or env var
        selected_model = (model_name or get_setting("AZURE_MODEL") or "azure-default").strip()
        model = LiteLlm(model=selected_model)
    else:
        raise ValueError(f"Unsupported LLM_PROVIDER: {provider}")

    return model
