from __future__ import annotations

import logging
from typing import Any

from google.adk.agents import LlmAgent

from agent.agents.agent_prompts import (
    AgentPromptStore,
    WIKI_CONTEXT_BLOCK,
    WIKI_RESEARCH_CHAT_AGENT_ID,
    render_template,
)
from agent.tools.pipeline.storage_backend import ArtifactStore
from agent.tools.pipeline.wiki_vector_index import retrieve_relevant_slugs


logger = logging.getLogger(__name__)

_DEFAULT_TOP_K = 3


def _read_page(artifact_store: ArtifactStore, athlete_id: int, slug: str) -> str | None:
    return artifact_store.read_text(f"wiki/{athlete_id}/{slug}.md")


def _list_wiki_slugs(artifact_store: ArtifactStore, athlete_id: int) -> list[str]:
    prefix = f"wiki/{athlete_id}/"
    slugs: list[str] = []
    for path in artifact_store.list_paths(prefix=prefix, suffix=".md"):
        slug = str(path).split("/")[-1].replace(".md", "")
        if slug and not slug.startswith("_"):
            slugs.append(slug)
    return sorted(slugs)


def _read_index(artifact_store: ArtifactStore, athlete_id: int) -> str | None:
    return artifact_store.read_text(f"wiki/{athlete_id}/_index.md")


def read_wiki_content(
    athlete_id: int,
    query: str | None = None,
    top_k: int = _DEFAULT_TOP_K,
) -> str | None:
    """Devuelve el contexto de la wiki relevante para la pregunta.

    - Si hay ``query`` y Pinecone está operativo: recupera las top_k páginas
      más relevantes por embedding y las concatena, junto con el ``_index``
      si existe (para orientación general y ahorro de tokens).
    - Si Pinecone no está configurado o falla: fallback al comportamiento
      antiguo de concatenar todas las páginas.
    - Devuelve ``None`` si el atleta no tiene ninguna página de wiki.
    """
    artifact_store = ArtifactStore()

    all_slugs = _list_wiki_slugs(artifact_store, athlete_id)
    if not all_slugs and _read_index(artifact_store, athlete_id) is None:
        return None

    selected_slugs: list[str] | None = None
    if query and query.strip():
        retrieved = retrieve_relevant_slugs(athlete_id, query, top_k=top_k)
        if retrieved:
            existing = set(all_slugs)
            filtered = [s for s in retrieved if s in existing]
            if filtered:
                selected_slugs = filtered
                logger.info(
                    "wiki RAG hit: athlete=%s query=%r -> %s",
                    athlete_id, query[:80], selected_slugs,
                )

    if selected_slugs is None:
        # Fallback: full wiki (Pinecone not configured, no matches, or not
        # yet indexed — the backend still works, just burns more tokens).
        selected_slugs = all_slugs

    sections: list[str] = []

    index_content = _read_index(artifact_store, athlete_id)
    if index_content:
        sections.append(f"<!-- página: _index -->\n{index_content}")

    for slug in selected_slugs:
        content = _read_page(artifact_store, athlete_id, slug)
        if content:
            sections.append(f"<!-- página: {slug} -->\n{content}")

    return "\n\n---\n\n".join(sections) if sections else None


# Backward-compatible alias
read_wiki_research_md = read_wiki_content


def build_wiki_research_chat_agent(
    *,
    selected_model: Any,
    wiki_content: str,
    athlete_id: int,
    instruction_template: str | None = None,
) -> LlmAgent:
    """Build an LlmAgent that answers user questions based on wiki content.

    ``instruction_template`` can override the prompt (for testing); otherwise
    the active template is loaded from the Firestore-backed ``agents``
    collection via :class:`AgentPromptStore`.
    """

    # Escape braces so the ADK template engine does not interpret {variable}
    # patterns in the wiki content as context variables.
    escaped_wiki = wiki_content.replace("{", "{{").replace("}", "}}")

    if instruction_template is None:
        instruction_template = AgentPromptStore().get_template(WIKI_RESEARCH_CHAT_AGENT_ID)

    render_values = {
        "%%ATHLETE_ID%%": str(athlete_id),
        "%%WIKI%%": escaped_wiki,
    }

    # If the template already embeds %%WIKI%% (self-contained prompt), render
    # its placeholders directly without prepending the shared context block —
    # which would duplicate the wiki content.  Otherwise use the standard path:
    # prepend WIKI_CONTEXT_BLOCK so the agent always gets athlete context.
    if "%%WIKI%%" in instruction_template:
        instruction = render_template(instruction_template, render_values)
    else:
        context_block = render_template(WIKI_CONTEXT_BLOCK, render_values)
        instruction = context_block + instruction_template

    return LlmAgent(
        name="wiki_research_chat_agent",
        model=selected_model,
        instruction=instruction,
    )
