from __future__ import annotations

import os
from typing import Any

from google.adk.agents import LlmAgent

try:
    from google.cloud import storage as gcs_storage
except Exception:  # noqa: BLE001
    gcs_storage = None

_WIKI_BUCKET_ENV = "GCS_WIKI_BUCKET"
_WIKI_BUCKET_DEFAULT = "adk-kb-bucket"


def _get_wiki_bucket_name() -> str:
    return (os.environ.get(_WIKI_BUCKET_ENV) or _WIKI_BUCKET_DEFAULT).strip()


def read_wiki_research_md(athlete_id: int) -> str | None:
    """Read wiki/athlete_id/research.md from the GCS wiki bucket.

    Returns the file content as a string, or None if the file does not exist
    or GCS is unavailable.
    """
    bucket_name = _get_wiki_bucket_name()
    blob_path = f"wiki/{athlete_id}/research.md"

    if gcs_storage is not None:
        try:
            client = gcs_storage.Client()
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(blob_path)
            if blob.exists():
                return blob.download_as_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    return None


def build_wiki_research_chat_agent(
    *,
    selected_model: Any,
    wiki_content: str,
    athlete_id: int,
) -> LlmAgent:
    """Build an LlmAgent that answers user questions based on wiki research content."""

    # Escape braces so the ADK template engine does not interpret {variable}
    # patterns in the wiki content as context variables. This must be done via
    # string concatenation (not f-string interpolation) so Python does not
    # convert {{ back to { before ADK sees the instruction.
    escaped_wiki = wiki_content.replace("{", "{{").replace("}", "}}")

    instruction = (
        "Eres un asistente experto en análisis de entrenamiento deportivo para atletas de Strava.\n\n"
        f"Se te ha proporcionado el informe de investigación (wiki) del atleta con ID {athlete_id}. "
        "Tu tarea es responder las preguntas del usuario EXCLUSIVAMENTE basándote en el contenido "
        "de ese informe. Si la información requerida no está en el informe, indícalo claramente.\n\n"
        "INFORME DE INVESTIGACIÓN (research.md):\n"
        "### INICIO DEL INFORME ###\n"
    )
    instruction += escaped_wiki
    instruction += (
        "\n### FIN DEL INFORME ###\n\n"
        "Instrucciones de respuesta:\n"
        "- Responde siempre en español.\n"
        "- Cita secciones relevantes del informe cuando sea útil.\n"
        "- Si la pregunta no puede responderse con el informe, explícalo brevemente y sugiere "
        "al usuario que ejecute una nueva pipeline de investigación para actualizar el wiki.\n"
        "- No inventes datos ni tendencias que no estén respaldadas por el informe.\n"
        "- Sé conciso pero completo."
    )

    return LlmAgent(
        name="wiki_research_chat_agent",
        model=selected_model,
        instruction=instruction,
    )
