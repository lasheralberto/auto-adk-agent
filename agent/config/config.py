import os
import pathlib

from dotenv import load_dotenv
from google.adk.skills import load_skill_from_dir
from google.adk.models.lite_llm import LiteLlm
from agent.tools.sandbox.sandbox_gcp_tool import LOCATION, PROJECT_ID

load_dotenv()

_SKILLS_DIR = pathlib.Path(__file__).parent.parent / "skills"


def _configure_vertex_backend() -> None:
    """Configure ADK/GenAI clients to use Vertex AI instead of API key mode."""
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", PROJECT_ID)
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", LOCATION)


_configure_vertex_backend()

# Memory provider configuration
MEMORY_PROVIDER = os.getenv("MEMORY_PROVIDER", "inmemory")
REDIS_URL = os.getenv("REDIS_URL", "")
SQLITE_MEMORY_DB_PATH = os.getenv("SQLITE_MEMORY_DB_PATH", ":memory:")

# ─── Load skills ──────────────────────────────────────────────────────────────
code_programmer_skill = load_skill_from_dir(_SKILLS_DIR / "code-programmer")
answer_agent_skill = load_skill_from_dir(_SKILLS_DIR / "answer-agent")
orchestrator_skill = load_skill_from_dir(_SKILLS_DIR / "orchestrator")
generic_scripts_skill = load_skill_from_dir(_SKILLS_DIR / "script-execution")
script_generator_skill = load_skill_from_dir(_SKILLS_DIR / "script-generator")
memory_agent_skill = load_skill_from_dir(_SKILLS_DIR / "memory-agent")
intent_router_skill = load_skill_from_dir(_SKILLS_DIR / "intent-router")
strava_agent_skill = load_skill_from_dir(_SKILLS_DIR / "strava-agent")


def get_llm_provider(llm_provider: str | None = None, model_name: str | None = None) -> LiteLlm:
    provider = (llm_provider or os.getenv("LLM_PROVIDER") or "").strip().lower()
    if provider not in {"google", "azure", "openai"}:
        raise ValueError(f"Unsupported LLM_PROVIDER: {provider}")

    if provider == "google":
        api_key = os.getenv("GOOGLE_API_KEY", "")
        if api_key:
            os.environ["GOOGLE_API_KEY"] = api_key
        selected_model = (model_name or os.getenv("GEMINI_MODEL") or "gemini-2.5-flash").strip()
        model = LiteLlm(model=selected_model)
    elif provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY", "")
        if api_key:
            os.environ["OPENAI_API_KEY"] = api_key
        selected_model = (model_name or os.getenv("OPENAI_MODEL") or "gpt-3.5-turbo").strip()
        model = LiteLlm(model=selected_model)
    else:
        raise ValueError(f"Unsupported LLM_PROVIDER: {provider}")

    return model
