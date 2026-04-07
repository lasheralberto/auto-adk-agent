import os
import pathlib

from google.adk.skills import load_skill_from_dir
from google.adk.models.lite_llm import LiteLlm
from agent.tools.sandbox.sandbox_gcp_tool import LOCATION, PROJECT_ID
from agent.config.envars import get_secret, get_setting, load_environment_from_sources

load_environment_from_sources()

_SKILLS_DIR = pathlib.Path(__file__).parent.parent / "skills"


def _configure_vertex_backend() -> None:
    """Configure ADK/GenAI clients to use Vertex AI instead of API key mode."""
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", PROJECT_ID)
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", LOCATION)


_configure_vertex_backend()

# Memory provider configuration
MEMORY_PROVIDER = get_setting("MEMORY_PROVIDER", "inmemory")
REDIS_URL = get_setting("REDIS_URL", "")
SQLITE_MEMORY_DB_PATH = get_setting("SQLITE_MEMORY_DB_PATH", ":memory:")

# ─── Load skills ──────────────────────────────────────────────────────────────
code_programmer_skill = load_skill_from_dir(_SKILLS_DIR / "code-programmer")
answer_agent_skill = load_skill_from_dir(_SKILLS_DIR / "answer-agent")
orchestrator_skill = load_skill_from_dir(_SKILLS_DIR / "orchestrator")
generic_scripts_skill = load_skill_from_dir(_SKILLS_DIR / "script-execution")
script_generator_skill = load_skill_from_dir(_SKILLS_DIR / "script-generator")
memory_agent_skill = load_skill_from_dir(_SKILLS_DIR / "memory-agent")
intent_router_skill = load_skill_from_dir(_SKILLS_DIR / "intent-router")
strava_agent_skill = load_skill_from_dir(_SKILLS_DIR / "strava-agent")
strava_coach_skill = load_skill_from_dir(_SKILLS_DIR / "strava-coach")
strava_formatter_skill = load_skill_from_dir(_SKILLS_DIR / "strava-formatter")
plan_react_planner_skill = load_skill_from_dir(_SKILLS_DIR / "plan-react-planner")


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
