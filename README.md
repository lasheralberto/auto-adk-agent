# Strava Agent SDK

SDK Python async para analizar atletas de Strava, generar wiki deportiva y consultar insights.

## Instalacion

```bash
git clone <repo-url> strava-agent-back
cd strava-agent-back
pip install -e .
```

## Autenticacion con service account JSON

Coloca el JSON de la service account en la raiz (p.ej. `strava-chat.json`) y pasalo al constructor:

```python
from strava_agent_sdk import StravaAgentClient

client = StravaAgentClient(
    gcp_project_id="strava-chat",
    gcp_credentials_path="./strava-chat.json",
)

info = client.check_gcp_auth()
# {"authenticated": True,
#  "identity": "153952529856-compute@developer.gserviceaccount.com",
#  "credential_type": "Credentials",
#  "project": "strava-chat"}
```

`check_gcp_auth()` es opcional: cualquier metodo de secrets verifica la autenticacion la primera vez que se llama.

Alternativas: `GOOGLE_APPLICATION_CREDENTIALS=/path/sa.json`, `gcloud auth application-default login`, o metadata server en Cloud Run / GCE / GKE (omite `gcp_credentials_path`).

## Cargar secretos desde Secret Manager

Al arrancar la app, vuelca los secretos a `os.environ`:

```python
client.load_secrets_into_env([
    "STRAVA_CLIENT_ID",
    "STRAVA_CLIENT_SECRET",
    "STRAVA_OAUTH_STATE_SECRET",
    "GCS_KNOWLEDGE_BUCKET",
    "GOOGLE_API_KEY",
    "PINECONE_API_KEY",
])
```

La SA runtime solo necesita `roles/secretmanager.secretAccessor`.

## Bootstrap inicial desde .env

Subir un `.env` a Secret Manager y dar acceso a la SA runtime en un solo paso (ejecutar una vez con credenciales de admin):

```python
client = StravaAgentClient(
    gcp_project_id="strava-chat",
    gcp_credentials_path="./admin-key.json",
)

client.create_secrets_from_env(
    ".env",
    grant_access_to="serviceAccount:153952529856-compute@developer.gserviceaccount.com",
)
```

## Uso rapido

```python
import asyncio
from strava_agent_sdk import StravaAgentClient

async def main():
    client = StravaAgentClient(
        gcp_project_id="strava-chat",
        gcp_credentials_path="./strava-chat.json",
    )
    client.load_secrets_into_env([
        "STRAVA_CLIENT_ID",
        "STRAVA_CLIENT_SECRET",
        "STRAVA_OAUTH_STATE_SECRET",
        "GCS_KNOWLEDGE_BUCKET",
        "GOOGLE_API_KEY",
        "PINECONE_API_KEY",
    ])

    auth = await client.start_strava_oauth(
        redirect_uri="https://miapp.com/auth/strava/callback",
        scope="read,activity:read_all,profile:read_all",
    )
    print(auth["auth_url"])

    res = await client.chat(
        question="¿Como voy esta semana?",
        athlete_id=12345,
        model_name="gemini-2.5-flash",
    )
    print(res.response)

asyncio.run(main())
```

## API publica

- **Auth Strava:** `start_strava_oauth`, `exchange_strava_code`, `refresh_strava_token`
- **Chat:** `chat`, `chat_stream`, `chat_wiki`, `chat_wiki_stream`, `query_wiki`
- **Pipeline:** `run_daily_pipeline`, `run_research_wiki`, `run_index_wiki`, `run_pipeline_stage`
- **Estado:** `list_athletes`, `get_pipeline_run`, `list_pipeline_runs`, `list_activity_runs`, `list_indexed_activities`, `get_indexing_status`
- **Secrets:** `get_secret`, `set_secret`, `delete_secret`, `list_secrets`, `load_secrets_into_env`, `create_secrets_from_env`, `grant_secret_access`, `check_gcp_auth`

## Permisos IAM

| Operacion | Rol minimo |
|---|---|
| `get_secret`, `load_secrets_into_env` | `roles/secretmanager.secretAccessor` |
| `list_secrets`, `check_gcp_auth` | `roles/secretmanager.viewer` |
| `set_secret`, `create_secrets_from_env` | `roles/secretmanager.admin` |
| `grant_secret_access` | `roles/secretmanager.admin` |

## Compatibilidad

`app.py` se mantiene como wrapper REST fino sobre el SDK.
