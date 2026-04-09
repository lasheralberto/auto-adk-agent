from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any

import requests

_INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
_DEEP_RESEARCH_AGENT = "deep-research-pro-preview-12-2025"
_POLL_INTERVAL_SECONDS = 10
_MAX_POLL_ATTEMPTS = 180  # 30 minutes max


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compact_record(record: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "_id",
        "id",
        "name",
        "sport_type",
        "distance",
        "moving_time",
        "total_elevation_gain",
        "average_speed",
        "average_heartrate",
        "max_heartrate",
        "average_watts",
        "kilojoules",
        "start_date",
        "start_date_local",
        "summary",
    ]
    return {key: record[key] for key in keys if record.get(key) is not None}


def _build_dataset_digest(
    *,
    athlete_id: int,
    target_date: str,
    window_days: int,
    target_records: list[dict[str, Any]],
    historical_records: list[dict[str, Any]],
    metrics: dict[str, Any] | None,
    athlete_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    profile = athlete_profile if isinstance(athlete_profile, dict) else {}
    return {
        "athlete_id": athlete_id,
        "target_date": target_date,
        "window_days": window_days,
        "metrics": metrics or {},
        "athlete_profile": {
            "id": profile.get("id") or athlete_id,
            "firstname": profile.get("firstname"),
            "lastname": profile.get("lastname"),
            "sex": profile.get("sex"),
            "city": profile.get("city"),
            "country": profile.get("country"),
        },
        "target_records": [_compact_record(item) for item in target_records[:200]],
        "historical_records": [_compact_record(item) for item in historical_records[:400]],
    }


def _build_research_prompt(digest: dict[str, Any], existing_report: str | None = None) -> str:
    athlete_id = digest.get("athlete_id")
    target_date = digest.get("target_date")
    window_days = digest.get("window_days")
    metrics = digest.get("metrics") or {}
    profile = digest.get("athlete_profile") or {}
    name = f"{profile.get('firstname', '')} {profile.get('lastname', '')}".strip() or f"Atleta {athlete_id}"

    if existing_report:
        existing_section = (
            f"INFORME EXISTENTE (generado en sesiones anteriores):\n"
            f"<existing_report>\n{existing_report}\n</existing_report>\n\n"
            "Tu tarea es ACTUALIZAR y MEJORAR el informe anterior incorporando los nuevos datos "
            f"de la fecha {target_date}. Mantén la información histórica relevante, actualiza las "
            "tendencias, y enriquece las recomendaciones con la evolución observada.\n\n"
        )
        task_description = (
            "Genera un informe ACTUALIZADO en español que combine el historial previo con los nuevos datos. "
            "El informe debe reflejar la evolución y progresión del atleta a lo largo del tiempo. "
        )
    else:
        existing_section = ""
        task_description = "Genera un informe completo en español. "

    return (
        f"Realiza un análisis profundo de entrenamiento para {name} (ID: {athlete_id}) "
        f"con fecha objetivo {target_date} y ventana de {window_days} días.\n\n"
        f"{existing_section}"
        f"MÉTRICAS RESUMEN:\n{json.dumps(metrics, ensure_ascii=False, indent=2)}\n\n"
        f"NUEVAS ACTIVIDADES (fecha objetivo + históricas recientes):\n"
        f"{json.dumps(digest, ensure_ascii=False)}\n\n"
        f"{task_description}"
        "El informe debe incluir las siguientes secciones:\n"
        "1. Rendimiento en la fecha objetivo\n"
        "2. Tendencias de entrenamiento y evolución temporal\n"
        "3. Distribución por tipo de deporte\n"
        "4. Señales de fatiga y recuperación\n"
        "5. Recomendaciones prácticas y próximos pasos\n\n"
        "Basa todas las conclusiones exclusivamente en los datos Strava proporcionados. "
        "No uses fuentes externas. Escribe en español.\n\n"
        "FORMATO OBLIGATORIO: Nunca uses notación LaTeX ni llaves {} en el informe. "
        "Expresa las fórmulas y cálculos en texto plano "
        "(ejemplo: 'v_media = d / t = 35846 m / 6615 s = 5.42 m/s', no '\\[ v_{media} = \\frac{d}{t} \\]'). "
        "Usa únicamente Markdown estándar (**, *, #, -, tablas). "
        "No incluyas ningún símbolo que pueda interpretarse como una variable de plantilla."
    )


def _post_interaction(api_key: str, prompt: str) -> str:
    response = requests.post(
        _INTERACTIONS_URL,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        json={
            "input": prompt,
            "agent": _DEEP_RESEARCH_AGENT,
            "background": True,
        },
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    interaction_id = data.get("id") or data.get("name", "").split("/")[-1]
    if not interaction_id:
        raise RuntimeError(f"No interaction ID returned: {data}")
    return interaction_id


def _get_interaction(api_key: str, interaction_id: str) -> dict[str, Any]:
    response = requests.get(
        f"{_INTERACTIONS_URL}/{interaction_id}",
        headers={"x-goog-api-key": api_key},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def _poll_until_complete(api_key: str, interaction_id: str) -> dict[str, Any]:
    for attempt in range(_MAX_POLL_ATTEMPTS):
        data = _get_interaction(api_key, interaction_id)
        state = str(data.get("state") or data.get("status") or "").upper()

        if state in {"COMPLETED", "SUCCEEDED", "DONE", "COMPLETE"}:
            return data

        if state in {"FAILED", "ERROR", "CANCELLED"}:
            raise RuntimeError(f"Interaction {interaction_id} ended with state {state}: {data}")

        if attempt < _MAX_POLL_ATTEMPTS - 1:
            time.sleep(_POLL_INTERVAL_SECONDS)

    raise RuntimeError(f"Interaction {interaction_id} did not complete after {_MAX_POLL_ATTEMPTS} polls")


def _extract_final_report(data: dict[str, Any]) -> str:
    for output in (data.get("outputs") or []):
        if isinstance(output, dict) and output.get("type") == "text":
            text = (output.get("text") or "").strip()
            if text:
                return text

    # Fallback: some responses use a top-level "response" or "text" field
    for field in ("response", "text", "result"):
        value = data.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()

    raise RuntimeError(f"No text output found in completed interaction: {list(data.keys())}")


def run_deep_research_wiki_agent(
    *,
    athlete_id: int,
    target_date: str,
    window_days: int,
    target_records: list[dict[str, Any]],
    historical_records: list[dict[str, Any]],
    metrics: dict[str, Any] | None = None,
    athlete_profile: dict[str, Any] | None = None,
    existing_report: str | None = None,
) -> dict[str, Any]:
    api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY environment variable is not set")

    digest = _build_dataset_digest(
        athlete_id=athlete_id,
        target_date=target_date,
        window_days=max(2, int(window_days)),
        target_records=target_records,
        historical_records=historical_records,
        metrics=metrics,
        athlete_profile=athlete_profile,
    )

    prompt = _build_research_prompt(digest, existing_report=existing_report)
    interaction_id = _post_interaction(api_key, prompt)
    completed = _poll_until_complete(api_key, interaction_id)
    final_report = _extract_final_report(completed)

    return {
        "athlete_id": athlete_id,
        "target_date": target_date,
        "window_days": max(2, int(window_days)),
        "interaction_id": interaction_id,
        "final_report": final_report,
        "evaluations": [],
        "created_at": _utc_now_iso(),
    }
