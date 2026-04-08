# Arquitectura v2: guia de endpoints

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
- Ejecuta todas las etapas en orden:
	1. ingestion
	2. activity_analysis
	3. daily_summary
	4. performance_insight
	5. wiki_builder
	6. embedding

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
- window_days (solo performance_insight, default 14)
- force_reindex (solo embedding, default false)

Stages soportados:
- ingestion
- activity_analysis
- daily_summary
- performance_insight
- wiki_builder
- embedding

Ejemplo:
```bash
curl -X POST http://localhost:8080/pipeline/stage \
	-H "Content-Type: application/json" \
	-H "X-Internal-Token: TU_TOKEN" \
	-d '{
		"stage": "embedding",
		"athlete_id": 123,
		"target_date": "2026-04-08",
		"force_reindex": true
	}'
```

### 8) Query semantica directa (sin chat)

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

### 9) Chat con RAG

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
- PINECONE_API_KEY
- PINECONE_INDEX_NAME
- PINECONE_EMBEDDING_MODEL
- PINECONE_NAMESPACE
- GOOGLE_CLOUD_PROJECT
- GOOGLE_CLOUD_LOCATION
- CORS_ALLOWED_ORIGINS
