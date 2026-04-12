"""Catálogo de páginas de la wiki de atleta.

Define la estructura de páginas especializadas que componen la wiki
de cada atleta. Cada actividad nueva puede afectar a varias de estas
páginas simultáneamente.
"""

from __future__ import annotations

WIKI_PAGES: list[dict[str, str]] = [
    {
        "slug": "fitness-profile",
        "name": "Perfil de Fitness",
        "description": (
            "Nivel de fitness actual estimado, FTP (ciclismo) o pace/LTHR de umbral (carrera), "
            "VO2max estimado, zonas de entrenamiento. Evolución en los últimos 30/90/365 días."
        ),
    },
    {
        "slug": "aerobic-base",
        "name": "Base Aeróbica",
        "description": (
            "Desarrollo de base aeróbica, trabajo en zona 2, eficiencia aeróbica, "
            "progresión de volumen a baja intensidad."
        ),
    },
    {
        "slug": "threshold-fitness",
        "name": "Fitness en Umbral",
        "description": (
            "Fitness en umbral de lactato, progresión de FTP o pace de umbral, "
            "sesiones de tempo y sweet-spot."
        ),
    },
    {
        "slug": "vo2max-development",
        "name": "Desarrollo VO2max",
        "description": (
            "Intervalos de alta intensidad, capacidad anaeróbica, trabajo por encima "
            "de umbral, progresión de VO2max."
        ),
    },
    {
        "slug": "recovery-patterns",
        "name": "Patrones de Recuperación",
        "description": (
            "HRV si disponible, recuperación entre sesiones, días de descanso, "
            "correlación entre recuperación y rendimiento posterior. "
            "Detección de sub-recuperación crónica."
        ),
    },
    {
        "slug": "fatigue-management",
        "name": "Gestión de Fatiga",
        "description": (
            "Training Stress Score (TSS), Chronic Training Load (CTL), "
            "Acute Training Load (ATL), Training Stress Balance (TSB). "
            "Estado de forma vs fatiga. Alertas de fatiga acumulada."
        ),
    },
    {
        "slug": "training-consistency",
        "name": "Consistencia de Entrenamiento",
        "description": (
            "Adherencia al plan, frecuencia semanal, volumen semanal, "
            "regularidad en la distribución de sesiones."
        ),
    },
    {
        "slug": "running-economy",
        "name": "Economía de Carrera",
        "description": (
            "Eficiencia de carrera, cadencia, relación pace/HR, "
            "zancada, progresión de economía a lo largo del tiempo. "
            "Solo relevante para actividades de carrera."
        ),
    },
    {
        "slug": "cycling-efficiency",
        "name": "Eficiencia en Ciclismo",
        "description": (
            "Potencia normalizada, Intensity Factor (IF), Variability Index (VI), "
            "eficiencia de pedaleo. Solo relevante para actividades de ciclismo."
        ),
    },
    {
        "slug": "power-profile",
        "name": "Perfil de Potencia",
        "description": (
            "Curva de potencia, W/kg por duración (5s, 1min, 5min, 20min, 60min), "
            "progresión temporal. Solo relevante si hay datos de potencia."
        ),
    },
    {
        "slug": "heart-rate-dynamics",
        "name": "Dinámica de Frecuencia Cardíaca",
        "description": (
            "Cardiac drift, decoupling HR:pace o HR:potencia, eficiencia cardíaca, "
            "respuesta HR a distintas intensidades."
        ),
    },
    {
        "slug": "load-progression",
        "name": "Progresión de Carga",
        "description": (
            "Progresión de carga de entrenamiento, regla del 10%, "
            "rampas de volumen e intensidad, periodización."
        ),
    },
    {
        "slug": "peak-performance-windows",
        "name": "Ventanas de Rendimiento Máximo",
        "description": (
            "Cuándo el atleta ha rendido mejor y por qué. "
            "Correlación entre carga previa, descanso y picos de rendimiento."
        ),
    },
    {
        "slug": "limiters-and-weaknesses",
        "name": "Limitantes y Debilidades",
        "description": (
            "Qué aspecto limita el rendimiento ahora: base aeróbica, umbral, "
            "economía, consistencia, recuperación. Se actualiza cuando los datos "
            "apuntan a un nuevo limitante o uno anterior se resuelve."
        ),
    },
    {
        "slug": "strong-points",
        "name": "Puntos Fuertes",
        "description": (
            "Puntos fuertes del atleta demostrados por datos: "
            "capacidades donde destaca respecto a su propio historial."
        ),
    },
    {
        "slug": "injury-risk-signals",
        "name": "Señales de Riesgo de Lesión",
        "description": (
            "Patrones que históricamente preceden lesión o bajadas abruptas: "
            "incrementos bruscos de carga, decoupling anómalo, cambios de cadencia, "
            "caídas de potencia con HR elevado."
        ),
    },
    {
        "slug": "nutrition-timing-hints",
        "name": "Indicios de Nutrición y Timing",
        "description": (
            "Señales de nutrición deducidas del rendimiento: caídas de energía, "
            "rendimiento en ayunas vs alimentado, patrones por hora del día."
        ),
    },
    {
        "slug": "race-readiness",
        "name": "Preparación para Competición",
        "description": (
            "Si hay eventos o carreras próximas, estimación de preparación, "
            "estado de tapering, predicción de rendimiento."
        ),
    },
    {
        "slug": "recommendations",
        "name": "Recomendaciones",
        "description": (
            "Recomendaciones activas priorizadas, específicas al estado actual. "
            "Cada recomendación tiene justificación basada en páginas concretas de la wiki."
        ),
    },
]

PAGE_SLUGS: set[str] = {page["slug"] for page in WIKI_PAGES}
PAGES_BY_SLUG: dict[str, dict[str, str]] = {page["slug"]: page for page in WIKI_PAGES}


def format_page_catalog_for_prompt() -> str:
    """Devuelve el catálogo de páginas formateado para incluir en prompts LLM."""
    lines: list[str] = []
    for i, page in enumerate(WIKI_PAGES, 1):
        lines.append(f"{i}. **{page['slug']}** — {page['name']}: {page['description']}")
    return "\n".join(lines)
