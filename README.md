# Strava Agent — Backend API

REST API + agente conversacional que sincroniza actividades de Strava, ejecuta ingesta diaria y pipeline de investigación wiki, y responde preguntas sobre el entrenamiento del atleta usando un LLM.

## Requisitos previos

- Python 3.10+
- Una app registrada en [strava.com/settings/api](https://www.strava.com/settings/api)
- Una API key de OpenAI **o** un proyecto de Google Cloud con Gemini habilitado

---

## Instalación local

```bash
git clone <repo-url>
cd strava-agent-back
pip install -r requirements.txt
```

Copia el archivo de ejemplo y rellena los valores:

```bash
cp .env.example .env
```

Arranca el servidor:

```bash
python app.py
# → http://localhost:8080
```

---

## Configuración — `.env`

### Strava OAuth (obligatorio)

```env
STRAVA_CLIENT_ID=         # ID numérico — strava.com/settings/api
STRAVA_CLIENT_SECRET=     # Client secret — strava.com/settings/api
STRAVA_OAUTH_STATE_SECRET=  # String aleatorio para protección CSRF
```

Registra las URLs de callback en la app de Strava:
- Local: `http://localhost:8080`
- Producción: la URL pública de tu API

### LLM Provider (elige uno)

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
GOOGLE_CLOUD_PROJECT=tu-proyecto
GOOGLE_CLOUD_LOCATION=us-central1
```

### Seguridad

```env
INTERNAL_PIPELINE_TOKEN=  # Token para proteger los endpoints /internal/*
CORS_ALLOWED_ORIGINS=     # Orígenes permitidos, separados por coma
```

### Storage y estado (opcional — por defecto usa disco local)

Sin estas variables el servidor guarda todo en `.knowledge_data/` localmente.

```env
# Google Cloud Firestore (estado OAuth y pipeline runs)
USE_FIRESTORE_STATE=true
PROJECT_ID=tu-proyecto

# Google Cloud Storage (knowledge base)
GCS_KNOWLEDGE_BUCKET=nombre-del-bucket
```

---

## Endpoints principales

| Método | Ruta | Descripción |
|--------|------|-------------|
| `POST` | `/ask` | Pregunta al agente conversacional |
| `GET` | `/auth/strava` | Inicia el flujo OAuth de Strava |
| `GET` | `/auth/strava/callback` | Callback OAuth de Strava |
| `POST` | `/pipeline/stage` | Ejecuta una etapa manual (`ingestion` o `research_wiki`) |
| `POST` | `/pipeline/daily` | Ejecuta ingesta diaria y encola `research_wiki` de forma asíncrona |
| `POST` | `/pipeline/research-wiki` | Ejecuta compilación de research wiki |
| `GET` | `/health` | Health check |

---

## Flujo real de `/pipeline/daily`

1. Ejecuta ingesta (`run_daily_pipeline -> ingestion`).
2. Devuelve reporte inicial con `steps.ingestion`.
3. Encola `/internal/pipeline/research-wiki` (no espera resultado final).
4. El estado de compilación wiki se revisa por runs y artefactos de investigación.

---

## Despliegue

### Docker

```bash
docker build -t strava-api .
docker run -p 8080:8080 --env-file .env strava-api
```

### Google Cloud Run

```bash
gcloud auth login
gcloud config set project TU_PROYECTO
gcloud beta builds submit   # usa cloudbuild.yaml incluido en el repo
```

---

## Resumen de variables

| Variable | Requerida | Descripción |
|----------|-----------|-------------|
| `STRAVA_CLIENT_ID` | Sí | ID de la app Strava |
| `STRAVA_CLIENT_SECRET` | Sí | Secret de la app Strava |
| `STRAVA_OAUTH_STATE_SECRET` | Sí | CSRF token (string aleatorio) |
| `LLM_PROVIDER` | Sí | `openai` o `google` |
| `OPENAI_API_KEY` | Si usa OpenAI | API key de OpenAI |
| `GOOGLE_API_KEY` | Si usa Gemini | API key de Google |
| `INTERNAL_PIPELINE_TOKEN` | Recomendada | Protege endpoints internos |
| `CORS_ALLOWED_ORIGINS` | Recomendada | Orígenes CORS permitidos |
| `USE_FIRESTORE_STATE` | No | `true` para usar Firestore en vez de JSON local |
| `GCS_KNOWLEDGE_BUCKET` | No | Bucket GCS para knowledge base |
