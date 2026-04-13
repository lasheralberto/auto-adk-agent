# Strava Agent API

> REST API conversacional para atletas de Strava. Ingesta actividades, construye una **wiki de conocimiento por atleta** con LLMs, la indexa en **Pinecone** (RAG) y responde preguntas sobre entrenamiento con *streaming*.

Desplegable en Docker o Google Cloud Run. Funciona con **OpenAI** o **Gemini** intercambiables.

---

## Tabla de contenidos

1. [Qué hace esta API](#1-qué-hace-esta-api)
2. [Quickstart (5 minutos)](#2-quickstart-5-minutos)
3. [Configuración completa](#3-configuración-completa)
4. [Flujo de autenticación Strava](#4-flujo-de-autenticación-strava)
5. [Cómo consumir la API](#5-cómo-consumir-la-api)
6. [Referencia completa de endpoints](#6-referencia-completa-de-endpoints)
7. [Despliegue en producción](#7-despliegue-en-producción)
8. [Cómo funciona por dentro](#8-cómo-funciona-por-dentro)

---

## 1. Qué hace esta API

Conectas tu cuenta de Strava → la API ingesta tus actividades → genera automáticamente una **wiki personal de entrenamiento** estructurada en 19 páginas (perfil fitness, gestión de fatiga, VO2max, recomendaciones, etc.) → puedes chatear con ella en lenguaje natural.

**Casos de uso típicos:**

- *"¿Cómo voy de forma esta semana comparado con el mes pasado?"*
- *"Dame un resumen de mis zonas aeróbicas y mi progresión de FTP"*
- *"¿Hay señales de sobreentrenamiento en mis últimas actividades?"*
- *"Recomiéndame entrenamientos para la carrera del mes que viene"*

La API expone endpoints REST (`/chat/wiki`, `/pipeline/daily`, `/auth/strava/*`...) para que los consumas desde cualquier cliente: web, app móvil, script Python, cron job, etc.

---

## 2. Quickstart (5 minutos)

### Requisitos

- Python 3.10+ (o Docker)
- Una app registrada en [strava.com/settings/api](https://www.strava.com/settings/api)
- Una API key de OpenAI **o** Gemini

### Paso 1 — Clonar e instalar

```bash
git clone <repo-url> strava-agent-back
cd strava-agent-back
pip install -r requirements.txt
```

### Paso 2 — Crear `.env`

```env
# Strava (obligatorio)
STRAVA_CLIENT_ID=12345
STRAVA_CLIENT_SECRET=tu_secret_de_strava
STRAVA_OAUTH_STATE_SECRET=cualquier_string_aleatorio_largo

# LLM — elige uno
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

# Opcional — protege endpoints internos
INTERNAL_PIPELINE_TOKEN=otro_token_aleatorio

# Opcional — CORS si la consumes desde un frontend
CORS_ALLOWED_ORIGINS=http://localhost:5173
```

Sin más configuración la API guarda todo en `.knowledge_data/` localmente (ideal para probar). Para producción, añade Firestore + GCS + Pinecone (ver [sección 3](#3-configuración-completa)).

### Paso 3 — Arrancar

```bash
python app.py
# → http://localhost:8080
```

Verifica:

```bash
curl http://localhost:8080/health
# {"status":"ok","architecture":"layered-pipeline-v2"}
```

### Paso 4 — Probar el flujo completo

```bash
# 1. Inicia OAuth — abre la auth_url que devuelve en el navegador
curl "http://localhost:8080/auth/strava/start?redirect_uri=http://localhost:8080/callback&scope=read,activity:read_all"

# 2. Tras autorizar, Strava redirige con ?code=XXX&state=YYY
#    Intercambia el code por tokens:
curl -X POST http://localhost:8080/auth/strava/exchange \
  -H "Content-Type: application/json" \
  -d '{"code":"XXX","state":"YYY","redirect_uri":"http://localhost:8080/callback"}'
# → {"access_token":"...","athlete":{"id":12345,...}}

# 3. Ingesta y construye la wiki
curl -X POST http://localhost:8080/pipeline/daily \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: <tu INTERNAL_PIPELINE_TOKEN>" \
  -d '{"athlete_id":12345,"latest_limit":10}'

# 4. Chatea con tu wiki
curl -X POST http://localhost:8080/chat/wiki \
  -H "Content-Type: application/json" \
  -d '{
    "message":"¿Cómo voy de fitness esta semana?",
    "athlete_id":12345,
    "llm_provider":"openai/gpt-4o-mini"
  }'
```

---

## 3. Configuración completa

Todas las variables se leen del entorno o de `.env`. Sin credenciales opcionales, la API cae a disco local de forma transparente.

### Strava OAuth (obligatorio)

| Variable | Descripción |
|----------|-------------|
| `STRAVA_CLIENT_ID` | ID numérico de tu app — [strava.com/settings/api](https://www.strava.com/settings/api) |
| `STRAVA_CLIENT_SECRET` | Client secret de la misma app |
| `STRAVA_OAUTH_STATE_SECRET` | String aleatorio largo para firma HMAC del `state` OAuth (protección CSRF) |
| `STRAVA_ALLOWED_REDIRECT_URIS` | CSV de redirect URIs permitidos. Sin esto se usa una lista por defecto local + vercel |

Registra las URLs de callback en la app de Strava (Authorization Callback Domain).

### LLM — elige uno

**OpenAI**
```env
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
```

**Google Gemini**
```env
LLM_PROVIDER=google
GOOGLE_API_KEY=AIza...
GEMINI_MODEL=gemini-2.5-flash
WIKI_LLM_MODEL=gemini-2.5-flash
```

El `llm_provider` también puede enviarse **por petición** (`"llm_provider":"openai/gpt-4o"`) para alternar modelos sin reiniciar.

### Storage — opcional (default = disco local)

Por defecto todo se guarda en `.knowledge_data/` del contenedor. Para producción multi-instancia:

```env
# Firestore — tokens, runs, cola de actividades
USE_FIRESTORE_STATE=true
PROJECT_ID=mi-proyecto-gcp
FIRESTORE_ATHLETES_COLLECTION=athletes
FIRESTORE_PIPELINE_RUNS_COLLECTION=pipeline_runs
FIRESTORE_ACTIVITY_RUNS_COLLECTION=activities_runs

# GCS — blobs de actividades y páginas de wiki markdown
GCS_KNOWLEDGE_BUCKET=mi-bucket
```

Requiere credenciales GCP (Application Default Credentials o `GOOGLE_APPLICATION_CREDENTIALS`).

### Pinecone — opcional (default = sin RAG)

Sin Pinecone, `/chat/wiki` funciona pero concatena toda la wiki en el prompt (gasta más tokens). Con Pinecone, solo recupera las 3 páginas más relevantes:

```env
PINECONE_API_KEY=tu_api_key
PINECONE_INDEX=strava-wiki
PINECONE_CLOUD=aws
PINECONE_REGION=us-east-1
WIKI_EMBEDDING_MODEL=llama-text-embed-v2
WIKI_EMBEDDING_DIMENSION=1024
```

El índice se crea automáticamente la primera vez. El embedding usa el modelo hospedado por Pinecone — no requiere infra propia.

### Seguridad

| Variable | Descripción |
|----------|-------------|
| `INTERNAL_PIPELINE_TOKEN` | Protege endpoints `/internal/*` y todos los `/pipeline/*`. Se envía como `X-Internal-Token: <token>`. Sin configurar, las rutas están abiertas (solo dev) |
| `CORS_ALLOWED_ORIGINS` | CSV de orígenes permitidos (ej. `https://miapp.com,http://localhost:5173`) |
| `INTERNAL_PIPELINE_BASE_URL` | URL base que la API usa para llamarse a sí misma cuando dispara pipelines async. En Cloud Run, la URL pública del servicio |

---

## 4. Flujo de autenticación Strava

La API implementa OAuth 2.0 de Strava completo con protección CSRF mediante `state` firmado con HMAC-SHA256.

```
Tu cliente                   Strava Agent API                Strava
    │                              │                           │
    │ GET /auth/strava/start       │                           │
    │   ?redirect_uri=&scope=      │                           │
    ├─────────────────────────────►│                           │
    │                              │ genera state HMAC         │
    │                              │   (TTL 10 min)            │
    │◄─────────────────────────────┤                           │
    │  { auth_url, state }         │                           │
    │                              │                           │
    │  navegador → auth_url                                    │
    ├─────────────────────────────────────────────────────────►│
    │                                                          │
    │  redirect a redirect_uri ?code=XXX&state=YYY             │
    │◄─────────────────────────────────────────────────────────┤
    │                              │                           │
    │ POST /auth/strava/exchange   │                           │
    │   { code, state, redirect_uri }                          │
    ├─────────────────────────────►│                           │
    │                              │ valida HMAC del state     │
    │                              │ POST oauth/token          │
    │                              ├──────────────────────────►│
    │                              │◄──────────────────────────┤
    │                              │ { access, refresh, ath }  │
    │                              │ upsert en Firestore       │
    │◄─────────────────────────────┤                           │
    │  tokens + perfil del atleta  │                           │
```

Los tokens se persisten internamente — tu cliente **no necesita enviar `access_token`** en cada petición de chat, basta con el `athlete_id`.

**Refresh**: cuando `expires_at - now < 60s`, llama `POST /auth/strava/refresh` con el `refresh_token`. La API también refresca automáticamente los tokens cuando los usa internamente para ingesta.

**Scopes válidos**: `read`, `read_all`, `profile:read_all`, `profile:write`, `activity:read`, `activity:read_all`, `activity:write`. Para análisis de entrenamiento necesitas como mínimo `activity:read_all`.

---

## 5. Cómo consumir la API

### 5.1 Escenario típico

```
1. /auth/strava/start       → obtienes auth_url
2. (usuario autoriza en Strava)
3. /auth/strava/exchange    → obtienes athlete_id
4. /pipeline/daily          → ingesta + construye wiki (primera vez tarda 1-2 min)
5. /chat/wiki               → chateas con la wiki (response en streaming)
```

Los pasos 1-3 son one-time por atleta. El paso 4 puedes automatizarlo con un cron diario. El paso 5 es el que tu cliente llama en cada interacción.

### 5.2 Chat con streaming (SSE)

El endpoint `/chat/wiki` soporta Server-Sent Events. Ejemplo en Python:

```python
import requests

response = requests.post(
    "https://tu-api.run.app/chat/wiki",
    headers={"Content-Type": "application/json"},
    json={
        "message": "Resume mi entrenamiento del último mes",
        "athlete_id": 12345,
        "llm_provider": "openai/gpt-4o-mini",
        "stream": True,
    },
    stream=True,
)

for line in response.iter_lines():
    if line.startswith(b"data: "):
        print(line[6:].decode())
```

Ejemplo en JavaScript:

```js
const res = await fetch(`${API}/chat/wiki`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    message: "¿Cómo voy de forma?",
    athlete_id: 12345,
    llm_provider: "openai/gpt-4o-mini",
    stream: true,
  }),
});

const reader = res.body.getReader();
const decoder = new TextDecoder();
while (true) {
  const { value, done } = await reader.read();
  if (done) break;
  console.log(decoder.decode(value));
}
```

Sin `stream: true` la respuesta es un JSON único con todo el texto.

### 5.3 Formato de respuesta del chat

**No-streaming:**

```json
{
  "response": "Tu fitness aeróbico está en progresión positiva...",
  "tool_calls": [],
  "athlete_id": 12345,
  "structured": { ... }
}
```

**Streaming (SSE):** eventos `data: {...}\n\n` con chunks incrementales del texto de respuesta.

**Error común — wiki no existe todavía:**

```json
{
  "error": "wiki_not_found",
  "details": "No se encontró la wiki para el atleta 12345. Ejecuta primero la pipeline de investigación."
}
```
→ Llama primero `/pipeline/daily` para construir la wiki.

### 5.4 Ejecutar la pipeline programáticamente

```bash
# Disparar pipeline diaria para un atleta (o varios)
curl -X POST $API/pipeline/daily \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: $TOKEN" \
  -d '{
    "athlete_ids": [12345, 67890],
    "latest_limit": 20
  }'
```

Respuesta inmediata (la pipeline continúa async en background):

```json
{
  "run_id": "4a9d8b...",
  "status": "running",
  "steps": {
    "ingestion": {
      "stage": "ingestion",
      "ok": true,
      "athletes": [{"athlete_id":12345,"new_activities":3,"skipped_existing":7}]
    },
    "research_wiki": {
      "stage": "research_wiki",
      "status": "queued",
      "endpoint": "https://.../internal/pipeline/research-wiki"
    },
    "index_wiki": { "status": "queued", "endpoint": "..." }
  }
}
```

Para seguir el progreso, haz polling:

```bash
curl "$API/pipeline/runs?stage=research_wiki&limit=1" \
  -H "X-Internal-Token: $TOKEN"
# status final: "success" | "skipped" | "partial_failure" | "failed"
```

---

## 6. Referencia completa de endpoints

Base URL: `http://localhost:8080` en local, tu URL de Cloud Run en producción.

Todos los endpoints marcados con 🔒 requieren header `X-Internal-Token: $INTERNAL_PIPELINE_TOKEN` si esa variable está configurada.

### Salud

#### `GET /health`

```bash
curl $API/health
```

```json
{"status":"ok","architecture":"layered-pipeline-v2"}
```

### Autenticación Strava

#### `GET /auth/strava/start`

Inicia el flujo OAuth. Devuelve la URL a la que debe redirigir tu cliente.

| Query param | Tipo | Descripción |
|-------------|------|-------------|
| `redirect_uri` | string (URL) | **requerido** — debe estar en `STRAVA_ALLOWED_REDIRECT_URIS` |
| `scope` | string | **requerido** — CSV de scopes, ej. `read,activity:read_all` |

```bash
curl "$API/auth/strava/start?redirect_uri=http://localhost:8080/cb&scope=read,activity:read_all"
```

```json
{
  "auth_url": "https://www.strava.com/oauth/authorize?...",
  "state": "eyJpYXQi...",
  "scope": "read,activity:read_all",
  "redirect_uri": "http://localhost:8080/cb"
}
```

#### `POST /auth/strava/exchange`

Intercambia el `code` que Strava devolvió por tokens.

```bash
curl -X POST $API/auth/strava/exchange \
  -H "Content-Type: application/json" \
  -d '{"code":"XXX","state":"YYY","redirect_uri":"http://localhost:8080/cb"}'
```

```json
{
  "access_token": "...",
  "refresh_token": "...",
  "expires_at": 1700000000,
  "expires_in": 21600,
  "scope": "read,activity:read_all",
  "athlete": { "id": 12345, "firstname": "...", "lastname": "..." }
}
```

#### `POST /auth/strava/refresh`

Renueva un access_token expirado.

```bash
curl -X POST $API/auth/strava/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token":"..."}'
```

### Chat

#### `POST /chat/wiki` — recomendado

RAG + streaming sobre la wiki del atleta. Usa Pinecone (top-3 páginas) si está configurado.

| Body field | Tipo | Descripción |
|------------|------|-------------|
| `message` / `question` | string | **requerido** — pregunta del usuario |
| `athlete_id` | int | **requerido** — atleta sobre el que consultar |
| `llm_provider` | string | `"openai/gpt-4o-mini"`, `"google/gemini-2.5-flash"`, etc. |
| `model` | string | Opcional — override del modelo |
| `stream` | bool | `true` → SSE, `false` → JSON único |

```bash
curl -X POST $API/chat/wiki \
  -H "Content-Type: application/json" \
  -d '{
    "message":"¿Cómo voy de fitness?",
    "athlete_id":12345,
    "llm_provider":"openai/gpt-4o-mini",
    "stream":false
  }'
```

Posibles errores:
- `400` — falta `message` o `athlete_id`
- `404 wiki_not_found` — el atleta aún no tiene wiki; llama `/pipeline/daily` primero

#### `POST /chat` · `POST /ask`

Orquestador ADK completo (intent router → planner → specialist agents). Más potente pero más lento. Usa `/chat/wiki` para chat normal.

| Body field extra | Tipo | Descripción |
|------------------|------|-------------|
| `strava_athlete_id` | int | Alias de `athlete_id` |
| `strava_access_token` | string | Opcional — inyecta el token en el contexto del agente |
| `planner_mode` | string | `"off"`, `"full_only"` (default), `"always"` |
| `response_format` | string | `"plan_react_v1"` (default), `"structured"`, `"plain"` |
| `top_k` | int | Default 5, max 20 — hits RAG |

### Pipelines 🔒

#### `POST /pipeline/daily` · `POST /internal/pipeline/daily` 🔒

El endpoint que un cron debería llamar diariamente. Ingesta nuevas actividades de Strava, las encola y dispara la construcción/actualización de la wiki asíncronamente.

| Body field | Tipo | Default | Descripción |
|------------|------|---------|-------------|
| `athlete_id` | int | — | Un solo atleta |
| `athlete_ids` | int[] | — | Varios atletas |
| `athlete_ids_csv` | string | — | `"12345,67890"` |
| `target_date` | string | hoy (UTC) | `"YYYY-MM-DD"` |

Sin `athlete_id*`, procesa **todos** los atletas con tokens válidos.

```bash
curl -X POST $API/pipeline/daily \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: $TOKEN" \
  -d '{"athlete_id":12345,"latest_limit":10}'
```

#### `POST /pipeline/stage` 🔒

Ejecuta una sola etapa manualmente.

```bash
# Solo ingesta
curl -X POST $API/pipeline/stage \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: $TOKEN" \
  -d '{"stage":"ingestion","athlete_id":12345,"latest_limit":20}'

# Solo wiki (procesa la cola de Firestore)
curl -X POST $API/pipeline/stage \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: $TOKEN" \
  -d '{"stage":"research_wiki","max_activities":50}'
```

Stages soportadas: `ingestion`, `research_wiki`.

#### `POST /pipeline/research-wiki` 🔒

Procesa actividades con `status="queued"` en Firestore: las pasa por el LLM y actualiza las páginas de la wiki afectadas.

```bash
curl -X POST $API/pipeline/research-wiki \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: $TOKEN" \
  -d '{"athlete_id":12345,"max_activities":50}'
```

#### `POST /pipeline/index-wiki` 🔒

Re-indexa en Pinecone todas las páginas existentes del atleta. Útil al cambiar de modelo de embedding o reconstruir el índice.

```bash
curl -X POST $API/pipeline/index-wiki \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: $TOKEN" \
  -d '{"athlete_id":12345}'
```

```json
{
  "stage": "index_wiki",
  "status": "success",
  "athletes_processed": 1,
  "total_pages_found": 19,
  "total_pages_indexed": 19,
  "results": [{"athlete_id":12345,"pages_indexed":19,"status":"ok"}]
}
```

#### `POST /pipeline/query`

Solo retrieval — sin LLM de chat. Devuelve los hits relevantes de la wiki.

```bash
curl -X POST $API/pipeline/query \
  -H "Content-Type: application/json" \
  -d '{"question":"fatiga acumulada","athlete_id":12345,"top_k":3}'
```

```json
{
  "mode": "wiki_research",
  "athlete_id": 12345,
  "hits": [
    {"score":0.5,"type":"wiki_research","source_path":"wiki/12345/fatigue-management.md","text":"..."},
    ...
  ],
  "context": "[wiki_research] ...",
  "source_path": "wiki/12345/"
}
```

### Observabilidad 🔒

#### `GET /athletes`

Lista atletas registrados.

```json
{
  "data": [{
    "athlete_id": 12345,
    "firstname": "Alberto",
    "last_sync_status": "success",
    "last_indexed_date": "2026-04-13"
  }],
  "count": 1,
  "state_mode": "firestore"
}
```

#### `GET /pipeline/run/<run_id>` 🔒

Detalle de un run concreto.

#### `GET /pipeline/runs?limit=20&stage=research_wiki` 🔒

Últimos runs, filtrables por stage.

#### `GET /pipeline/activities-runs?athlete_id=12345&limit=20`

Actividades del atleta con su estado (`queued`, `running`, `success`, `partial_success`, `failed`).

#### `GET /pipeline/indexed-activities?athlete_id=12345&limit=20`

Solo actividades indexadas (estado `success` o `partial_success`).

#### `GET /pipeline/indexing-status?athlete_id=12345`

Snapshot rápido para el badge de la UI:

```json
{
  "athlete_id": 12345,
  "today": "2026-04-13",
  "last_indexed_date": "2026-04-13",
  "indexed_today": true,
  "last_sync_status": "success"
}
```

### Rutas deprecadas (HTTP 410)

`/vector_stores`, `/add_to_vs`, `/strava/weekly-summary`, `/search_vs`, `/vectorize`, `/get_vs_file_*`, `/delete_vs_file` — existían en una versión anterior. Devuelven 410 con un payload que redirige a las rutas nuevas.

---

## 7. Despliegue en producción

### Docker

```bash
docker build -t strava-api .
docker run -p 8080:8080 --env-file .env strava-api
```

El `Dockerfile` usa `gunicorn` con `--worker-class gthread --timeout 0` para soportar streaming SSE largo.

### Google Cloud Run

El repo incluye [`cloudbuild.yaml`](./cloudbuild.yaml) con el pipeline completo (build → push → deploy).

```bash
gcloud auth login
gcloud config set project TU_PROYECTO
gcloud beta builds submit
```

Secretos que debes crear en Secret Manager previamente:

- `GOOGLE_API_KEY`
- `OPENAI_API_KEY`
- `STRAVA_CLIENT_SECRET`
- `STRAVA_OAUTH_STATE_SECRET`
- `INTERNAL_PIPELINE_TOKEN`
- `PINECONE_API_KEY`

Se montan automáticamente con `--set-secrets` en el deploy.

### Cron / tareas programadas

Una única llamada diaria a `/pipeline/daily` por atleta (o sin `athlete_id` para todos a la vez) basta. Ejemplo con Cloud Scheduler:

```bash
gcloud scheduler jobs create http strava-daily \
  --schedule="0 6 * * *" \
  --uri="https://tu-api.run.app/pipeline/daily" \
  --http-method=POST \
  --headers="X-Internal-Token=$TOKEN,Content-Type=application/json" \
  --message-body='{"latest_limit":20}'
```

---

## 8. Cómo funciona por dentro

Esta sección es para curiosos. **No necesitas entenderla para consumir la API.**

### 8.1 Arquitectura global

```
     Tu cliente
         │ HTTPS
         ▼
┌────────────────────────────────────────────────────┐
│           Flask API (gunicorn gthread)             │
│  ┌─────────────────────────────────────────────┐   │
│  │ Orchestrator (Google ADK)                   │   │
│  │ ├─ intent_router                            │   │
│  │ ├─ plan_react_planner                       │   │
│  │ ├─ strava_ingestion_agent                   │   │
│  │ ├─ query_agent                              │   │
│  │ └─ answer_agent                             │   │
│  └─────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────┐   │
│  │ Pipelines                                   │   │
│  │ ingestion · research_wiki · index · daily   │   │
│  └─────────────────────────────────────────────┘   │
└────┬───────────┬─────────────┬────────────────────┘
     ▼           ▼             ▼            ▼
 Strava API  Firestore       GCS        Pinecone
 (OAuth)     athletes        raw/       strava-wiki
            pipeline_runs    wiki/       ns:ath_{id}
          activities_runs
```

### 8.2 Pipeline de datos end-to-end

```
Strava API
   │   fetch_latest_activities(limit=10)
   ▼
[Stage 1: Ingestion]  (run_strava_ingestion)
   │   - escribe blob GCS: pipeline/research-wiki-input/{activity_id}
   │   - crea doc Firestore activities_runs/{activity_id} status=queued
   ▼
[Stage 2: Research Wiki]  (research_wiki_pipeline) — async
   │   Para cada actividad queued:
   │   - triage_activity(LLM) → decide qué páginas afecta
   │   - update_page(LLM) → integra evidencia en cada página
   │   - escribe wiki/{athlete_id}/{slug}.md en GCS
   │   - upsert vector a Pinecone (ns: ath_{athlete_id})
   │   - marca actividad como status=success
   ▼
[Stage 3: Index Wiki]  (backfill_athlete) — opcional
   │   Re-indexa en Pinecone todas las páginas (útil tras cambio de modelo)
   ▼
[Stage 4: Chat]  (GET /chat/wiki)
   │   - retrieve_relevant_slugs(athlete_id, query, top_k=3) ← Pinecone
   │   - concatena esas páginas + _index.md
   │   - LLM responde en streaming
   ▼
Respuesta SSE al cliente
```

### 8.3 La wiki del atleta

Cada atleta tiene una carpeta `wiki/{athlete_id}/` en GCS con ~19 páginas markdown especializadas:

```
wiki/12345/
├── _index.md                  # resumen ejecutivo
├── _log.md                    # log append-only
├── fitness-profile.md
├── aerobic-base.md
├── threshold-fitness.md
├── vo2max-development.md
├── recovery-patterns.md
├── fatigue-management.md      # TSS/CTL/ATL/TSB
├── training-consistency.md
├── running-economy.md
├── cycling-efficiency.md      # NP/IF/VI
├── power-profile.md
├── heart-rate-dynamics.md
├── load-progression.md
├── peak-performance-windows.md
├── limiters-and-weaknesses.md
├── strong-points.md
├── injury-risk-signals.md
├── nutrition-timing-hints.md
├── race-readiness.md
└── recommendations.md
```

Cada página es redactada por el LLM como **análisis de coach**, no como dump de datos. Catálogo completo en [`agent/tools/pipeline/wiki_pages.py`](./agent/tools/pipeline/wiki_pages.py). Estructura por página en [`AGENTS.md`](./AGENTS.md).

### 8.4 Modelo de datos

| Colección Firestore | Doc ID | Contenido |
|--------------------|--------|-----------|
| `athletes` | `{athlete_id}` | Tokens OAuth, perfil, last_sync_status |
| `pipeline_runs` | `{run_id UUID}` | Stages, status, timings |
| `activities_runs` | `{activity_id}` | Cola de actividades (queued/running/success/failed) |

Bucket GCS:

```
gs://<bucket>/
├── pipeline/research-wiki-input/{activity_id}   # raw JSON Strava
└── wiki/{athlete_id}/*.md                       # páginas de wiki
```

Pinecone:

- Index `strava-wiki`, serverless AWS us-east-1, cosine, dim=1024
- Namespace por atleta: `ath_{athlete_id}`
- Una página = un vector (id: `{athlete_id}:{slug}`)

### 8.5 Decisiones de diseño destacables

1. **Firestore como cola de trabajo** — la colección `activities_runs` actúa como cola persistente, registro de auditoría y fuente para el progreso del UI. No requiere Pub/Sub ni Cloud Tasks.
2. **Deduplicación por path de blob** — el `activity_id` es el nombre del fichero en GCS. Listar el prefijo deduplica en O(1) sin consultar Firestore.
3. **Una página = un vector** — las páginas ya son unidades semánticas coherentes redactadas por LLM; cambiar una página = un único upsert a Pinecone.
4. **Fallback silencioso GCS/Firestore → disco** — sin credenciales, arranca igualmente y escribe en `.knowledge_data/`. El campo `mode` en cada respuesta indica `"gcs"` o `"local"`.
5. **LLM intercambiable por petición** — `llm_provider` en el body sobrescribe el global; puedes A/B testear OpenAI vs Gemini sin redeploy.
6. **HMAC OAuth state sin sesión server-side** — el `state` es auto-contenido (firma + TTL); no requiere Redis.
7. **Skills como assets versionados** — los prompts viven en `agent/skills/*/SKILL.md` con frontmatter YAML, revisables en pull request como código.

### 8.6 Estructura del repo

```
strava-agent-back/
├── app.py                      # Flask app: routing + OAuth
├── Dockerfile
├── cloudbuild.yaml
├── requirements.txt
└── agent/
    ├── app.py                  # build_orchestrator() - ensambla 5 agentes ADK
    ├── runner.py               # run_agent / run_agent_streaming (SSE)
    ├── config/                 # get_llm_provider() multi-proveedor
    ├── agents/
    │   └── wiki_research_chat_agent.py   # RAG chat
    ├── skills/                 # Prompts declarativos (SKILL.md)
    ├── service/
    │   └── stream_utils.py     # Serialización SSE
    └── tools/pipeline/
        ├── workflow.py                    # run_ingestion, research_wiki_pipeline, run_query_layer
        ├── storage_backend.py             # ArtifactStore (GCS) + AthleteStateStore (Firestore)
        ├── wiki_llm.py                    # LLM ops: bootstrap / triage / update / index
        ├── wiki_pages.py                  # Catálogo de 19 páginas
        ├── wiki_vector_index.py           # Pinecone: index, retrieve, backfill
        └── connectors/
            ├── base.py                    # DataConnector (interfaz abstracta)
            └── strava.py                  # StravaConnector (OAuth + fetch)
```
