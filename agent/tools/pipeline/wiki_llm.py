"""Motor LLM para la wiki de atleta.

Reemplaza la integración con Google Deep Research API por llamadas
directas a litellm.completion(). Ofrece:

- Triage: determinar qué páginas afecta una nueva actividad.
- Update: actualizar una página individual integrando nueva evidencia.
- Bootstrap: crear toda la wiki desde la primera actividad.
- Index: regenerar el resumen ejecutivo (_index.md).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import litellm

from .wiki_pages import PAGE_SLUGS, PAGES_BY_SLUG, WIKI_PAGES, format_page_catalog_for_prompt


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_model() -> str:
    return (os.environ.get("WIKI_LLM_MODEL") or "gemini-2.5-flash").strip()


def _call_llm(
    system: str,
    user: str,
    json_mode: bool = False,
) -> str:
    """Llamada genérica a litellm.completion()."""
    model = _get_model()
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.3,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    response = litellm.completion(**kwargs)
    return response.choices[0].message.content or ""


def _parse_json_response(raw: str) -> Any:
    """Extrae JSON de la respuesta del LLM, tolerando bloques ```json."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # Remove opening ``` line
        lines = lines[1:]
        # Remove closing ``` if present
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return json.loads(text)


# ─── Triage ──────────────────────────────────────────────────────────────────


_TRIAGE_SYSTEM = f"""\
Eres un científico del deporte experto analizando datos de entrenamiento de Strava.
Tu tarea es determinar qué páginas de la wiki del atleta necesitan actualizarse
tras una nueva actividad.

Páginas disponibles:
{format_page_catalog_for_prompt()}

Reglas:
- Solo selecciona páginas donde la nueva actividad aporta información relevante.
- Una actividad de ciclismo NO debe afectar running-economy.
- Una actividad de carrera NO debe afectar cycling-efficiency ni power-profile (salvo que tenga datos de potencia).
- Incluye siempre fitness-profile para cualquier actividad.
- Incluye siempre training-consistency para cualquier actividad.
- Incluye fatigue-management si hay datos de duración o carga.
- Devuelve SOLO JSON válido, sin texto adicional."""

_TRIAGE_USER = """\
Resumen actual de la wiki (_index.md):
<index>
{index_content}
</index>

Datos de la nueva actividad:
<activity>
{activity_json}
</activity>

¿Qué páginas necesitan actualizarse? Responde con JSON:
{{"pages": [{{"slug": "nombre-de-pagina", "reason": "razón breve"}}]}}"""


def triage_activity(activity_data: dict[str, Any], index_content: str) -> list[str]:
    """Determina qué páginas de la wiki afecta una nueva actividad."""
    user_msg = _TRIAGE_USER.format(
        index_content=index_content,
        activity_json=json.dumps(activity_data, ensure_ascii=False),
    )
    raw = _call_llm(_TRIAGE_SYSTEM, user_msg, json_mode=True)

    try:
        parsed = _parse_json_response(raw)
    except (json.JSONDecodeError, KeyError):
        # Fallback: actualizar todas las páginas
        return list(PAGE_SLUGS)

    pages = parsed.get("pages") if isinstance(parsed, dict) else []
    if not isinstance(pages, list):
        return list(PAGE_SLUGS)

    slugs = [
        entry["slug"]
        for entry in pages
        if isinstance(entry, dict) and entry.get("slug") in PAGE_SLUGS
    ]
    return slugs if slugs else list(PAGE_SLUGS)


# ─── Update page ─────────────────────────────────────────────────────────────


_UPDATE_SYSTEM = """\
Eres un analista de entrenamiento deportivo actualizando la página "{page_name}" \
de la wiki de un atleta.

Propósito de esta página: {page_description}

Reglas:
- Integra los nuevos datos con el contenido existente. NO borres análisis histórico.
- Si la página no existe aún, crea el contenido inicial basándote en la actividad.
- Escribe en español.
- Usa Markdown estricto: ## para secciones, ### para subsecciones, - para listas, **negrita** para etiquetas.
- Expresa fórmulas en texto plano (no LaTeX, no llaves {{}}).
- Basa todas las conclusiones en los datos proporcionados.
- Sé conciso pero completo: prioriza datos y tendencias sobre texto genérico.
- Devuelve SOLO el contenido Markdown actualizado de la página, sin explicaciones."""

_UPDATE_USER = """\
Atleta: {athlete_name}

Contenido actual de la página:
<current_page>
{current_content}
</current_page>

Nueva actividad ({target_date}):
<activity>
{activity_json}
</activity>

Métricas resumen:
<metrics>
{metrics_json}
</metrics>

Genera el contenido actualizado completo de la página en Markdown."""


def update_page(
    slug: str,
    current_content: str | None,
    activity_data: dict[str, Any],
    athlete_name: str,
) -> str:
    """Actualiza una página individual de la wiki con nueva evidencia."""
    page_info = PAGES_BY_SLUG.get(slug, {"name": slug, "description": ""})
    system = _UPDATE_SYSTEM.format(
        page_name=page_info["name"],
        page_description=page_info["description"],
    )

    record = activity_data.get("record", {})
    metrics = activity_data.get("metrics", {})
    target_date = activity_data.get("target_date", "")

    user_msg = _UPDATE_USER.format(
        athlete_name=athlete_name,
        current_content=current_content or "Esta página aún no existe. Crea el contenido inicial.",
        target_date=target_date,
        activity_json=json.dumps(record, ensure_ascii=False),
        metrics_json=json.dumps(metrics, ensure_ascii=False),
    )
    return _call_llm(system, user_msg)


# ─── Bootstrap (primera actividad) ──────────────────────────────────────────


_BOOTSTRAP_SYSTEM = f"""\
Eres un analista de entrenamiento deportivo creando la wiki inicial de un atleta \
de Strava basándote en su primera actividad registrada.

Crea contenido para TODAS estas páginas:
{format_page_catalog_for_prompt()}

Además, genera un _index con un resumen ejecutivo y catálogo de todas las páginas.

Reglas:
- Escribe en español.
- Sé breve: es una sola actividad, no hay historial aún.
- Marca secciones como "Datos insuficientes — se completará con más actividades" \
donde una sola actividad no permita análisis.
- Usa Markdown estricto.
- Expresa fórmulas en texto plano (no LaTeX, no llaves {{}}).
- Devuelve SOLO JSON válido con esta estructura:
  {{"_index": "markdown del index", "fitness-profile": "markdown", "aerobic-base": "markdown", ...}}
- Incluye TODAS las páginas del catálogo en el JSON."""

_BOOTSTRAP_USER = """\
Atleta: {athlete_name}
Perfil: {profile_json}

Primera actividad ({target_date}):
<activity>
{activity_json}
</activity>

Métricas:
<metrics>
{metrics_json}
</metrics>

Genera el JSON con el contenido de todas las páginas de la wiki."""


def bootstrap_wiki(activity_data: dict[str, Any], athlete_name: str) -> dict[str, str]:
    """Genera toda la wiki desde la primera actividad del atleta."""
    record = activity_data.get("record", {})
    metrics = activity_data.get("metrics", {})
    profile = activity_data.get("athlete_profile", {})
    target_date = activity_data.get("target_date", "")

    user_msg = _BOOTSTRAP_USER.format(
        athlete_name=athlete_name,
        profile_json=json.dumps(profile, ensure_ascii=False),
        target_date=target_date,
        activity_json=json.dumps(record, ensure_ascii=False),
        metrics_json=json.dumps(metrics, ensure_ascii=False),
    )
    raw = _call_llm(_BOOTSTRAP_SYSTEM, user_msg, json_mode=True)

    try:
        pages = _parse_json_response(raw)
    except (json.JSONDecodeError, KeyError) as exc:
        raise RuntimeError(f"bootstrap_wiki: JSON parse error: {exc}") from exc

    if not isinstance(pages, dict):
        raise RuntimeError(f"bootstrap_wiki: expected dict, got {type(pages).__name__}")

    return {key: str(value) for key, value in pages.items()}


# ─── Index ───────────────────────────────────────────────────────────────────


_INDEX_SYSTEM = """\
Eres un analista de entrenamiento deportivo regenerando el índice (_index.md) \
de la wiki de un atleta.

El índice es un resumen ejecutivo que actúa como catálogo de todo el conocimiento \
acumulado del atleta. Incluye:
- Resumen del estado actual del atleta en 3-5 líneas.
- Lista de cada página de la wiki con: link, resumen de una línea, última fecha de datos.
- Organizado por categorías: Fitness, Carga, Eficiencia, Riesgo, Recomendaciones.

Reglas:
- Escribe en español.
- Usa Markdown estricto.
- Devuelve SOLO el contenido Markdown del _index.md."""

_INDEX_USER = """\
Atleta: {athlete_name}

Contenido resumido de cada página existente:
<pages>
{pages_summary}
</pages>

Genera el _index.md actualizado."""


def generate_index(page_summaries: dict[str, str], athlete_name: str) -> str:
    """Regenera _index.md con el estado actual de todas las páginas."""
    summary_lines: list[str] = []
    for slug in [p["slug"] for p in WIKI_PAGES]:
        content = page_summaries.get(slug)
        if content:
            # Tomar las primeras ~500 chars como resumen
            preview = content[:500].strip()
            summary_lines.append(f"### {slug}\n{preview}\n")
        else:
            summary_lines.append(f"### {slug}\n(Página no creada aún)\n")

    user_msg = _INDEX_USER.format(
        athlete_name=athlete_name,
        pages_summary="\n".join(summary_lines),
    )
    return _call_llm(_INDEX_SYSTEM, user_msg)


# ─── Log entry ───────────────────────────────────────────────────────────────


def format_log_entry(
    date: str,
    action: str,
    activity_summary: str,
    affected_pages: list[str],
) -> str:
    """Formatea una entrada para _log.md (sin LLM, puro string)."""
    pages_str = ", ".join(affected_pages) if affected_pages else "ninguna"
    return f"## [{date}] {action} | {activity_summary} | {pages_str}\n"
