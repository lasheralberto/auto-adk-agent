from __future__ import annotations

from typing import Any

from google.adk.agents import LlmAgent

from agent.tools.pipeline.storage_backend import ArtifactStore


def read_wiki_content(athlete_id: int) -> str | None:
    """Lee todas las páginas de la wiki del atleta y las concatena.

    Devuelve el contenido combinado como string, o None si no existe ninguna
    página en la wiki del atleta.
    """
    artifact_store = ArtifactStore()
    prefix = f"wiki/{athlete_id}/"
    paths = artifact_store.list_paths(prefix=prefix, suffix=".md")
    if not paths:
        return None

    sections: list[str] = []
    for page_path in sorted(paths):
        content = artifact_store.read_text(page_path)
        if content:
            slug = str(page_path).split("/")[-1].replace(".md", "")
            sections.append(f"<!-- página: {slug} -->\n{content}")

    return "\n\n---\n\n".join(sections) if sections else None


# Backward-compatible alias
read_wiki_research_md = read_wiki_content


def build_wiki_research_chat_agent(
    *,
    selected_model: Any,
    wiki_content: str,
    athlete_id: int,
) -> LlmAgent:
    """Build an LlmAgent that answers user questions based on wiki content."""

    # Escape braces so the ADK template engine does not interpret {variable}
    # patterns in the wiki content as context variables.
    escaped_wiki = wiki_content.replace("{", "{{").replace("}", "}}")

    instruction = (
        "Eres un asistente experto en análisis de entrenamiento deportivo para atletas de Strava.\n\n"
        f"Se te ha proporcionado la wiki completa del atleta con ID {athlete_id}. "
        "La wiki contiene múltiples páginas especializadas (perfil de fitness, gestión de fatiga, "
        "recomendaciones, etc.) que se actualizan automáticamente con cada nueva actividad.\n\n"
        "WIKI DEL ATLETA:\n"
        "### INICIO DE LA WIKI ###\n"
    )
    instruction += escaped_wiki
    instruction += "\n### FIN DE LA WIKI ###\n"

    instruction += (
        "\n\nInstrucciones de respuesta:\n"
        "- Responde siempre en español.\n"
        "- Basa tus respuestas en la información de la wiki proporcionada.\n"
        "- Cuando cites datos, indica de qué página de la wiki provienen.\n"
        "- Si la pregunta no puede responderse con la información disponible, explícalo brevemente "
        "y sugiere al usuario que ejecute una nueva pipeline de investigación para actualizar la wiki.\n"
        "- No inventes datos ni tendencias que no estén respaldadas por las fuentes proporcionadas.\n"
        "- Sé conciso pero completo."
    )

    return LlmAgent(
        name="wiki_research_chat_agent",
        model=selected_model,
        instruction=instruction,
    )
