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
<<<<<<< HEAD
strava_agent_skill = load_skill_from_dir(_SKILLS_DIR / "strava-agent")

=======
sd_agent_skill = load_skill_from_dir(_SKILLS_DIR / "sd-agent")
fi_agent_skill = load_skill_from_dir(_SKILLS_DIR / "fi-agent")
sap_technical_skill = load_skill_from_dir(_SKILLS_DIR / "sap-technical-agent")
cloudification_skill = load_skill_from_dir(_SKILLS_DIR / "cloudification-agent")
>>>>>>> e72f17c02742dfffd00f7332afb8aea5928227a8

def get_llm_provider(llm_provider: str | None = None, model_name: str | None = None) -> LiteLlm:
    # Prefer the explicit parameter `llm_provider` (used when called from /ask).
    # If it's not provided, fall back to environment variables (for import-time initialization).
    provider_raw = llm_provider if llm_provider else (os.getenv("LLM_PROVIDER") or os.getenv("LLM") or "")
    if not provider_raw:
        raise ValueError("Unsupported LLM_PROVIDER: None. Set LLM_PROVIDER in .env or pass via request payload.")

    # Support combined format 'provider/model' (e.g. 'openai/gpt-4o')
    provider_parts = provider_raw.split("/", 1)
    provider = provider_parts[0].strip().lower()
    if len(provider_parts) > 1 and not model_name:
        model_name = provider_parts[1].strip()

    if provider == "google":
        api_key = os.getenv("GOOGLE_API_KEY", "")
        if api_key:
            os.environ["GOOGLE_API_KEY"] = api_key
            print("Using GOOGLE_API_KEY from environment variable.")
        selected_model = (model_name or os.getenv("GEMINI_MODEL") or "gemini-2.5-flash").strip()
        model = LiteLlm(model=selected_model)
    elif provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY", "")
        if api_key:
            os.environ["OPENAI_API_KEY"] = api_key
            print("Using OPENAI_API_KEY from environment variable.")
        selected_model = (model_name or os.getenv("OPENAI_MODEL") or "gpt-3.5-turbo").strip()
        model = LiteLlm(model=selected_model)
    elif provider == "azure":
        # Azure handling could go here if needed; reuse model_name or env var
        selected_model = (model_name or os.getenv("AZURE_MODEL") or "azure-default").strip()
        model = LiteLlm(model=selected_model)
    else:
        raise ValueError(f"Unsupported LLM_PROVIDER: {provider}")

    return model
<<<<<<< HEAD
=======


# Do not call `get_llm_provider()` at import time — the LLM provider and model
# will be provided at runtime via the request payload. Keep `MODEL` as None
# to avoid raising on import when no provider is configured in env.
MODEL = None
>>>>>>> e72f17c02742dfffd00f7332afb8aea5928227a8
