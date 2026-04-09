# Arquitectura v2: guia de endpoints

## Esquema tecnico del proceso

### Vision general

```
┌─────────────────────────────────────────────────────────────────┐
│                        FRONTEND / CLIENTE                        │
└────────────────────────┬────────────────────────────────────────┘
                         │ HTTP / SSE
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                    FastAPI (agent/app.py)                        │
│  /auth/*   /pipeline/*   /chat   /ask   /athletes   /health     │
└────┬───────────────┬─────────────────┬───────────────────────────┘
     │               │                 │
     ▼               ▼                 ▼
┌─────────┐  ┌──────────────┐  ┌──────────────────────────────┐
│  OAuth  │  │   Pipeline   │  │     Orquestador de Chat       │
│  Flow   │  │   Runner     │  │  (plan_react_v1 / structured) │
└────┬────┘  └──────┬───────┘  └──────────────┬───────────────┘
     │              │                          │
     ▼              ▼                          ▼
┌──────────┐ ┌───────────────────────┐  ┌───────────────┐
│  Strava  │ │  Pipeline por etapas  │  │ Intent Router │
│  OAuth2  │ │  (secuencial)         │  └───────┬───────┘
│  API     │ └──────────┬────────────┘          │
└──────────┘            │              ┌─────────┴──────────┐
                        │              │                    │
                        ▼              ▼                    ▼
              ┌─────────────────┐ EARLY_RESPONSE    FULL_EXECUTION
              │ 4 etapas:       │  (conversacional)  (con tools)
              │ 1. ingestion    │
              │ 2. pinecone_    │
              │    indexing     │
              │ 3. rag_wiki     │
              │ 4. research_    │
              │    wiki (async) │
              └────────┬────────┘
                       │
          ┌────────────┼────────────┐
          ▼                    ▼
   ┌────────────┐    ┌──────────────────────┐
   │  Firestore │    │        GCS           │
   │  (estado / │    │  (storage raw, docs, │
   │  atletas)  │    │   wiki, indice local)│
   └────────────┘    └──────────────────────┘
```

### Flujo detallado: Pipeline diario

```
POST /pipeline/daily
        │
        ▼
 Resolver athlete_ids
 (body → Firestore)
        │
        ▼
┌───────────────────────────────────────────────────────┐
│  Por cada atleta:                                     │
│                                                       │
│  [1] ingestion-agent                                  │
│      └─ Strava API → actividades raw → GCS            │
│                                                       │
│  [2] pinecone-indexing-agent                          │
│      └─ raw GCS → summaries + upsert payload          │
│      └─ payload -> Pinecone                           │
│                                                       │
│  [3] rag-wiki-agent                                   │
│      └─ contexto Pinecone -> wiki Markdown            │
│                                                       │
│  [4] research-wiki-agent (asincrono)                  │
│      └─ input de upsert + ventana historica           │
│      └─ reporte deep research -> adk-kb-bucket/wiki   │
│                                                       │
│  Estado de cada etapa → Firestore                     │
└───────────────────────────────────────────────────────┘
```

### Flujo detallado: Chat con RAG

```
POST /chat  {message, athlete_id, llm_provider, ...}
        │
        ▼
 Intent Router
  ├─ EARLY_RESPONSE ──► respuesta directa LLM
  └─ FULL_EXECUTION
        │
        ▼
 Query Layer (run_query_layer)
  └─ question → busqueda lexica → indice local top-k → contexto
        │
        ▼
 Orquestador (plan_react_v1 | structured | plain)
  └─ contexto RAG + mensaje → LLM → respuesta
        │
        ▼
 Respuesta (JSON o SSE si stream=true)
  {response, tool_calls, retrieval_hits, structured}
```

### Flujo detallado: OAuth Strava

```
GET /auth/strava/start?redirect_uri=...
        │
        └─► genera state (TTL 10min) → devuelve auth_url
                    │
                    ▼ (usuario aprueba en Strava)
POST /auth/strava/exchange  {code, state, redirect_uri}
        │
        └─► valida state → intercambia code en Strava API
            → guarda tokens por athlete_id (Firestore)
            → devuelve access_token + athlete

POST /auth/strava/refresh  {refresh_token}
        │
        └─► renueva token → actualiza Firestore → devuelve nuevo access_token
```

### Componentes de infraestructura

| Componente | Tecnologia | Uso |
|---|---|---|
| Backend API | FastAPI (Python) | Endpoints HTTP/SSE |
| Orquestacion LLM | Google ADK / LiteLLM | Multi-provider (Gemini, OpenAI, etc.) |
| Estado / atletas | Firestore | Tokens, sync status, pipeline runs |
| Storage de documentos | GCS | Actividades raw, analisis, wiki |
| Despliegue | Cloud Run (Docker) | Produccion GCP |



Este documento esta orientado a uso operativo: que hace cada endpoint, que enviar y que esperar.

## URL base

- Local: http://localhost:8080
- Produccion: la URL de tu servicio Cloud Run

## Autorizacion interna (pipeline y listado de atletas)

Los endpoints internos usan esta regla:

- Si INTERNAL_PIPELINE_TOKEN NO esta configurado, no piden token.
- Si INTERNAL_PIPELINE_TOKEN SI esta configurado, debes enviar uno de estos headers:
	- X-Internal-Token: <token>
	- Authorization: Bearer <token>

## Resumen rapido de endpoints

- GET /health: healthcheck del backend.
- GET /athletes: lista atletas con tokens guardados y estado de sync/index.
- GET /auth/strava/start: genera auth_url y state para iniciar OAuth en frontend.
- POST /auth/strava/exchange: intercambia code por tokens y guarda el atleta.
- POST /auth/strava/refresh: refresca access_token.
- POST /pipeline/daily (y /internal/pipeline/daily): corre pipeline completo de un dia.
- POST /pipeline/research-wiki (y /internal/pipeline/research-wiki): ejecuta investigacion profunda sobre datos Strava del dia/ventana.
- POST /pipeline/stage (y /internal/pipeline/stage): corre solo una etapa.
- POST /pipeline/query: consulta semantica directa (RAG) por atleta.
- POST /chat y POST /ask: chat con orquestador + contexto RAG.

## Endpoints en detalle

### 1) Health

Endpoint:
- GET /health

Que hace:
- Verifica que el servicio esta arriba.

Respuesta esperada:
```json
{
	"status": "ok",
	"architecture": "layered-pipeline-v2"
}
```

### 2) Listar atletas

Endpoint:
- GET /athletes

Auth:
- Interna (ver seccion de autorizacion interna).

Que hace:
- Devuelve los atletas con tokens almacenados y metadatos de sync/index.

Respuesta (ejemplo):
```json
{
	"data": [
		{
			"athlete_id": 123,
			"firstname": "Alberto",
			"lastname": "Martinez",
			"country": "ES",
			"last_sync_epoch": 1744050000,
			"last_sync_status": "ok",
			"last_indexed_date": "2026-04-08",
			"token_updated_at": "2026-04-08T12:00:00Z"
		}
	],
	"count": 1,
	"state_mode": "firestore"
}
```

### 3) Iniciar OAuth de Strava

Endpoint:
- GET /auth/strava/start

Query params:
- redirect_uri (requerido)
- scope (opcional, default: read,activity:read_all,profile:read_all)

Que hace:
- Crea state temporal (TTL ~10 min) y devuelve auth_url para redirigir al usuario a Strava.

Ejemplo:
```bash
curl "http://localhost:8080/auth/strava/start?redirect_uri=http://localhost:5173/auth/strava/callback"
```

Respuesta (ejemplo):
```json
{
	"auth_url": "https://www.strava.com/oauth/authorize?...",
	"state": "abc123...",
	"scope": "read,activity:read_all,profile:read_all",
	"redirect_uri": "http://localhost:5173/auth/strava/callback"
}
```

### 4) Intercambiar code de OAuth

Endpoint:
- POST /auth/strava/exchange

Body:
- code (requerido)
- state (requerido)
- redirect_uri (requerido)

Que hace:
- Intercambia el code en Strava, retorna tokens y guarda tokens por athlete_id.

Ejemplo:
```bash
curl -X POST http://localhost:8080/auth/strava/exchange \
	-H "Content-Type: application/json" \
	-d '{
		"code": "STRAVA_CODE",
		"state": "STATE_DEVUELTO_EN_START",
		"redirect_uri": "http://localhost:5173/auth/strava/callback"
	}'
```

Respuesta:
- token_type, access_token, refresh_token, expires_at, expires_in, scope, athlete

### 5) Refrescar token de Strava

Endpoint:
- POST /auth/strava/refresh

Body:
- refresh_token (requerido)
- strava_athlete_id (opcional, recomendado si Strava no devuelve athlete)

Que hace:
- Renueva access_token y actualiza el storage de tokens.

### 6) Correr pipeline diario completo

Endpoints equivalentes:
- POST /pipeline/daily
- POST /internal/pipeline/daily

Auth:
- Interna (si hay INTERNAL_PIPELINE_TOKEN).

Body (todos opcionales):
- athlete_ids: [123, 456]
- athlete_ids_csv: "123,456"
- athlete_id: 123
- target_date: "YYYY-MM-DD"
- lookback_days: int (default 7, rango 1..30)
- window_days: int (default 14, rango 2..60)

Que hace:
- Ejecuta etapas sincronas en orden:
	1. ingestion
	2. pinecone_indexing
	3. rag_wiki
- Luego dispara de forma asincrona `research_wiki` via endpoint interno.

Ejemplo:
```bash
curl -X POST http://localhost:8080/pipeline/daily \
	-H "Content-Type: application/json" \
	-H "X-Internal-Token: TU_TOKEN" \
	-d '{
		"athlete_ids": [123],
		"target_date": "2026-04-08",
		"lookback_days": 7,
		"window_days": 14
	}'
```

### 7) Correr una etapa puntual

Endpoints equivalentes:
- POST /pipeline/stage
- POST /internal/pipeline/stage

Auth:
- Interna (si hay INTERNAL_PIPELINE_TOKEN).

Body:
- stage (requerido)
- athlete_ids | athlete_ids_csv | athlete_id (opcionales)
- target_date (opcional)
- lookback_days (solo ingestion, default 7)
- window_days (solo research_wiki, default 14)
- daily_run_id (opcional, usado por research_wiki para trazabilidad)

Stages soportados:
- ingestion
- pinecone_indexing
- rag_wiki
- research_wiki

Ejemplo:
```bash
curl -X POST http://localhost:8080/pipeline/stage \
	-H "Content-Type: application/json" \
	-H "X-Internal-Token: TU_TOKEN" \
	-d '{
		"stage": "research_wiki",
		"athlete_id": 123,
		"target_date": "2026-04-08",
		"window_days": 14
	}'
```

### 8) Deep Research Wiki (ejecucion directa)

Endpoints equivalentes:
- POST /pipeline/research-wiki
- POST /internal/pipeline/research-wiki

Auth:
- Interna (si hay INTERNAL_PIPELINE_TOKEN).

Body:
- athlete_ids | athlete_ids_csv | athlete_id (opcionales)
- target_date (opcional)
- window_days (opcional, default 14, rango 2..60)
- daily_run_id (opcional)

Que hace:
- Usa como input el payload preparado para upsert en Pinecone en target_date.
- No consulta Pinecone para recuperar contexto de investigacion.
- Completa un analisis profundo de rendimiento, datos de entrenamiento, mejoras y consejos.
- Sube resultados a `gs://adk-kb-bucket/wiki/{athlete_id}/{yyyy-mm-dd}/research.md`.

Ejemplo:
```bash
curl -X POST http://localhost:8080/pipeline/research-wiki \
	-H "Content-Type: application/json" \
	-H "X-Internal-Token: TU_TOKEN" \
	-d '{
		"athlete_id": 123,
		"target_date": "2026-04-08",
		"window_days": 14
	}'
```

### 9) Query semantica directa (sin chat)

Endpoint:
- POST /pipeline/query

Body:
- question o message (requerido)
- athlete_id o strava_athlete_id (requerido)
- top_k (opcional, default 5, rango 1..20)
- target_date (opcional)

Que hace:
- Ejecuta run_query_layer y devuelve contexto/hits para ese atleta.

Ejemplo:
```bash
curl -X POST http://localhost:8080/pipeline/query \
	-H "Content-Type: application/json" \
	-d '{
		"question": "Como estuvo mi carga esta semana?",
		"athlete_id": 123,
		"top_k": 5
	}'
```

### 10) Chat con RAG

Endpoints equivalentes:
- POST /chat
- POST /ask

Body minimo:
- message o question (requerido)
- llm_provider (requerido, ejemplo: "openai/gpt-4o")
- athlete_id o strava_athlete_id (requerido)

Body opcional:
- model
- stream (true/false)
- top_k (default 5, rango 1..20)
- target_date
- response_format: plan_react_v1 | structured | plain (default plan_react_v1)
- planner_mode: off | full_only | always (default full_only)
- strava_access_token o access_token

Notas:
- Si envias Authorization: Bearer <token>, puede usarse como access token de Strava.
- El backend no expone el token al usuario final en la respuesta de texto.
- Si stream=true, la respuesta es SSE (text/event-stream).

Respuesta no streaming (campos principales):
- response
- tool_calls
- structured (si aplica)
- api_version (si aplica)
- retrieval_hits
- query_mode

Ejemplo no streaming:
```bash
curl -X POST http://localhost:8080/chat \
	-H "Content-Type: application/json" \
	-d '{
		"message": "Que debo ajustar para bajar fatiga?",
		"llm_provider": "openai/gpt-4o",
		"athlete_id": 123,
		"response_format": "structured",
		"planner_mode": "full_only"
	}'
```

## Endpoints deprecados (410)

Estos endpoints ya no se usan y devuelven 410 Gone:

- /vector_stores
- /add_to_vs
- /strava/weekly-summary
- /search_vs
- /vectorize
- /get_vs_file_details
- /get_vs_file_content
- /delete_vs_file

Debes migrar a:

- chat: /chat o /ask
- query directa: /pipeline/query
- pipeline completo: /pipeline/daily
- pipeline por etapa: /pipeline/stage

## Flujo recomendado (end-to-end)

1. Frontend llama GET /auth/strava/start y redirige al auth_url.
2. Callback llama POST /auth/strava/exchange y guarda tokens del atleta.
3. Trigger inicial: POST /pipeline/daily para poblar conocimiento.
4. Experiencia de usuario: POST /chat (o /ask) para respuestas con contexto RAG.
5. Mantenimiento: cron con /internal/pipeline/daily.

## Variables de entorno clave

- STRAVA_CLIENT_ID
- STRAVA_CLIENT_SECRET
- INTERNAL_PIPELINE_TOKEN
- USE_FIRESTORE_STATE
- FIRESTORE_ATHLETES_COLLECTION
- FIRESTORE_PIPELINE_RUNS_COLLECTION
- GCS_KNOWLEDGE_BUCKET o STRAVA_KNOWLEDGE_BUCKET
- LOCAL_KNOWLEDGE_ROOT
- GOOGLE_CLOUD_PROJECT
- GOOGLE_CLOUD_LOCATION
- CORS_ALLOWED_ORIGINS
