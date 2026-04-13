## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- After modifying code files in this session, run `python3 -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"` to keep the graph current

---

## Propósito del proyecto

**Athlete Intelligence Wiki** — un sistema que construye y mantiene una base de conocimiento deportiva personalizada por atleta, usando LLMs, alimentada por datos de Strava y almacenada en Google Cloud Storage.

### Idea central

La mayoría de apps de análisis deportivo hacen algo parecido a RAG: miran actividades recientes y generan un resumen cada vez desde cero. No hay acumulación ni memoria histórica.

Este proyecto es diferente: un agente en background lee periódicamente las actividades de Strava, extrae señales relevantes e integra ese conocimiento en una **wiki persistente por atleta** — una colección estructurada de `.md` en GCS que actúa como el cuaderno de un coach profesional.

Cuando llegan nuevas actividades, el agente **no** genera un análisis de usar y tirar. Lee lo que ya sabe sobre el atleta, actualiza las páginas afectadas, detecta tendencias nuevas, revisa hipótesis anteriores y fortalece o corrige la síntesis acumulada.

---

## Arquitectura

| Componente | Rol |
|---|---|
| **Strava API** | Fuente de verdad inmutable. Solo lectura: actividades, streams de telemetría (HR, potencia, cadencia, elevación, velocidad), perfil del atleta (FTP, zonas, peso), segmentos. |
| **GCS `raw/{athlete_id}/`** | Copia local de datos Strava en JSON. Inmutable. Cada actividad como `activity_{id}.json`. El agente lee desde aquí para no abusar de la API. |
| **GCS `wiki/{athlete_id}/`** | Conocimiento compilado. Archivos `.md` que el agente crea y mantiene. El conocimiento acumulado no desaparece: se compone. |
| **Firestore** | Índice del grafo de la wiki: metadatos de cada página, backlinks entre documentos, log de cambios del agente, alertas activas. |
| **Agente LLM** | Corre en background periódicamente (Cloud Run Job). Lee actividades nuevas, guarda en `raw/`, actualiza la wiki según el schema de `AGENTS.md`, sincroniza Firestore. |

### Estructura de carpetas GCS

```
tu-bucket/
├── raw/
│   └── {athlete_id}/
│       ├── athlete_profile.json
│       ├── activity_{id}.json          # actividad completa con streams
│       └── segment_{id}.json
│
├── wiki/
│   └── {athlete_id}/
│       ├── _index.md                   # resumen ejecutivo del atleta (orientado a contenido)
│       ├── _log.md                     # log cronológico append-only de acciones del agente
│       ├── fitness-profile.md
│       ├── aerobic-base.md
│       ├── threshold-fitness.md
│       ├── vo2max-development.md
│       ├── recovery-patterns.md
│       ├── fatigue-management.md       # TSS, CTL, ATL, TSB
│       ├── training-consistency.md
│       ├── running-economy.md
│       ├── cycling-efficiency.md
│       ├── power-profile.md
│       ├── heart-rate-dynamics.md
│       ├── load-progression.md
│       ├── peak-performance-windows.md
│       ├── limiters-and-weaknesses.md
│       ├── strong-points.md
│       ├── injury-risk-signals.md
│       ├── nutrition-timing-hints.md
│       ├── race-readiness.md
│       └── recommendations.md
│
└── schema/
    └── AGENTS.md
```

---

## Workflows del agente

### Sync (cada N horas)
1. Consultar Strava API, detectar actividades nuevas desde el último sync
2. Guardar en `raw/{athlete_id}/`
3. Actualizar `_log.md`

### Analyze (tras cada sync con datos nuevos)
1. Leer actividades nuevas desde `raw/`
2. Contrastar con páginas wiki existentes
3. Decidir qué páginas actualizar (una sola sesión puede afectar múltiples páginas simultáneamente)
4. Actualizar cada página **integrando** la nueva evidencia con la acumulada — nunca sobreescribir

### Synthesize (semanal)
1. Leer todas las páginas `wiki/{athlete_id}/*.md`
2. Detectar y resolver contradicciones entre páginas
3. Actualizar `_index.md` con el estado actual del atleta
4. Revisar vigencia de `recommendations.md`
5. Evaluar limitantes nuevos o resueltos en `limiters-and-weaknesses.md`

### Lint (mensual)
Auditar la wiki: páginas desactualizadas, hipótesis sin confirmar, limitantes ya resueltos, patrones nuevos sin página propia.

---

## Indexing y logging

- **`_index.md`** — orientado a contenido. Catálogo de todo lo que se sabe del atleta: cada página con link, resumen de una línea y metadatos (última actualización, nº actividades analizadas, nivel de confianza). El agente lo lee **primero** para orientarse antes de profundizar en páginas relevantes.

- **`_log.md`** — cronológico, append-only. Formato:
  ```
  ## [2026-04-10] sync | 3 actividades nuevas | athlete_12345
  ## [2026-04-10] analyze | Ride 120min Z2 + threshold | fitness-profile, fatigue-management
  ## [2026-04-07] synthesize | Revisión semanal | limiters actualizados: base aeróbica → umbral
  ## [2026-04-01] alert | Señal de sobreentrenamiento detectada | TSB: -28
  ```

---

## Métricas clave a calcular

- **TSS** = (seg × NP × IF) / (FTP × 3600) × 100
- **CTL** (42 días), **ATL** (7 días), **TSB** = CTL − ATL
- **Decoupling aeróbico** = (pace:HR primera mitad vs segunda mitad) / pace:HR primera mitad
- **Eficiencia de potencia** = NP / HR medio
- **VO2max estimado** = 15 × (HRmax / HRrest) [Uth-Sørensen] o por pace en umbral

---

## Principios de diseño

- Una instalación mínima viable es: Strava API + GCS + agente semanal + `_index.md` como único mecanismo de navegación. Firestore, búsqueda vectorial y webhooks se añaden cuando el tamaño lo justifica.
- El agente **siempre integra** nueva evidencia con la existente; nunca sobreescribe sin justificar.
- Cuando una observación nueva contradice una anterior, se marca la contradicción explícitamente y se actualiza la conclusión con los datos más recientes.
- Cada afirmación en la wiki incluye las actividades que la justifican (IDs de Strava).
- Las páginas se escriben como análisis de coach, no como dumps de datos.
