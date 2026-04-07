import os
import re
from dotenv import load_dotenv
from google import genai
from openai import OpenAI
from agent.config.envars import get_secret, get_setting


def _strip_markdown_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9_\-]*\n", "", cleaned)
        cleaned = re.sub(r"\n```$", "", cleaned)
    return cleaned.strip()


def _build_generation_prompt(question: str) -> str:
    return (
        "You generate production-ready Python scripts for direct execution.\n"
        "Return only valid Python source code and nothing else.\n"
        "Do not include markdown code fences.\n"
        "If the task can fail, include basic error handling and clear stdout output.\n"
        "You may use any required Python libraries and external resources to solve the task.\n\n"
        f"User request: {question}\n"
    )


def _extract_text_parts(response: object) -> str:
    candidates = getattr(response, "candidates", None) or []
    texts: list[str] = []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        if not content:
            continue
        parts = getattr(content, "parts", None) or []
        for part in parts:
            text = getattr(part, "text", None)
            if text:
                texts.append(text)
    return "\n".join(texts).strip()


def generate_script(question: str, model: str | None = None) -> str:
    """Generate Python code from user question using chosen LLM and return code as plain string."""
    if not question or not question.strip():
        raise ValueError("question cannot be empty")

    load_dotenv()
    provider = str(get_setting("LLM_PROVIDER", "google")).lower()

    if provider == "openai":
        model_name = model or get_setting("OPENAI_MODEL") or "gpt-5.4"
        client = OpenAI()
        response = client.responses.create(
            model=model_name,
            input=_build_generation_prompt(question),
            reasoning={"effort": "high"},
        )
        raw_text = response.output_text
    else:
        # Default to google/genai
        os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
        model_name = model or get_setting("GEMINI_MODEL") or "gemini-2.5-flash"

        api_key = get_secret("GOOGLE_API_KEY")
        if api_key:
            os.environ["GEMINI_API_KEY"] = api_key
        client = genai.Client()

        response = client.models.generate_content(
            model=model_name,
            contents=_build_generation_prompt(question),
        )
        raw_text = _extract_text_parts(response)

    script_source = _strip_markdown_fences(raw_text)
    if not script_source:
        raise ValueError("Model returned empty script source")

    compile(script_source, "generated_script.py", "exec")
    return script_source
