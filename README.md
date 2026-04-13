# Strava Agent SDK

SDK Python async para analizar atletas de Strava, generar wiki deportiva y consultar insights.

## Instalacion

```bash
git clone <repo-url> strava-agent-back
cd strava-agent-back
pip install -e .
```

## Variables de entorno

```env
# Strava (obligatorio)
STRAVA_CLIENT_ID=12345
STRAVA_CLIENT_SECRET=tu_secret
STRAVA_OAUTH_STATE_SECRET=string_largo_seguro

# Storage wiki (obligatorio)
GCS_KNOWLEDGE_BUCKET=mi-bucket
# o STRAVA_KNOWLEDGE_BUCKET=mi-bucket

# LLM (recomendado)
GOOGLE_API_KEY=AIza...
GEMINI_MODEL=gemini-2.5-flash

# Estado (opcional)
USE_FIRESTORE_STATE=true
PROJECT_ID=mi-proyecto-gcp
```

Usa `model_name` al llamar metodos del cliente.

## Uso rapido

```python
import asyncio
from strava_agent_sdk import StravaAgentClient

async def main():
    client = StravaAgentClient()

    # OAuth
    auth = await client.start_strava_oauth(
        redirect_uri="https://miapp.com/auth/strava/callback",
        scope="read,activity:read_all,profile:read_all",
    )
    print(auth["auth_url"])

    # Chat
    res = await client.chat(
        question="¿Como voy esta semana?",
        athlete_id=12345,
        model_name="gemini-2.5-flash",
    )
    print(res.response)

asyncio.run(main())
```

## API publica

- Auth:
`start_strava_oauth`, `exchange_strava_code`, `refresh_strava_token`
- Chat:
`chat`, `chat_stream`, `chat_wiki`, `chat_wiki_stream`, `query_wiki`
- Pipeline:
`run_daily_pipeline`, `run_research_wiki`, `run_index_wiki`, `run_pipeline_stage`
- Estado:
`list_athletes`, `get_pipeline_run`, `list_pipeline_runs`, `list_activity_runs`, `list_indexed_activities`, `get_indexing_status`
- Secrets (GCP Secret Manager):
`get_secret`, `set_secret`, `delete_secret`, `list_secrets`, `create_secrets_from_env`, `load_secrets_into_env`, `grant_secret_access`, `check_gcp_auth`

## Autenticacion con Google Cloud

Los metodos de `Secrets` (y cualquier otra integracion con GCP) usan **Application Default Credentials (ADC)**. El SDK no gestiona credenciales por ti; solo las consume.

### Opciones disponibles (en orden de resolucion)

1. **`credentials_path` explicito** en el constructor — la via mas simple:
   ```python
   client = StravaAgentClient(
       gcp_project_id="strava-chat",
       gcp_credentials_path="./sa-key.json",
   )
   ```

2. **Variable `GOOGLE_APPLICATION_CREDENTIALS`** apuntando a un JSON de service account:
   ```bash
   export GOOGLE_APPLICATION_CREDENTIALS=/path/sa-key.json
   ```

3. **Login de usuario con gcloud** (recomendado en local):
   ```bash
   gcloud auth application-default login
   ```

4. **Metadata server** — automatico en Cloud Run, GCE, GKE, Cloud Functions. No hay que configurar nada: se usa la service account adjunta al servicio.

### Project ID

Se resuelve desde `gcp_project_id` (constructor) o, si no se pasa, desde la variable `GOOGLE_CLOUD_PROJECT`.

### Verificar que la autenticacion funciona

```python
client = StravaAgentClient(gcp_project_id="strava-chat")
info = client.check_gcp_auth()
# {"authenticated": True, "identity": "...@strava-chat.iam.gserviceaccount.com",
#  "credential_type": "Credentials", "project": "strava-chat"}
```

Si no hay credenciales o no tienen acceso al proyecto, lanza `ExternalServiceError` con un mensaje accionable (que comando ejecutar o que variable definir).

### Permisos IAM requeridos

| Operacion | Rol minimo sobre el secreto o proyecto |
|---|---|
| `get_secret` | `roles/secretmanager.secretAccessor` |
| `set_secret`, `create_secrets_from_env` | `roles/secretmanager.secretVersionAdder` + `roles/secretmanager.admin` para crear |
| `grant_secret_access` | `roles/secretmanager.admin` |
| `list_secrets`, `check_gcp_auth` | `roles/secretmanager.viewer` |

Tipicamente la SA runtime de la app solo necesita `secretAccessor`. Las operaciones de bootstrap (`create_secrets_from_env`, `grant_secret_access`) deberian ejecutarse con credenciales de admin (usuario o SA de CI), no desde la app en produccion.

### Ejemplo: bootstrap de secretos desde .env

```python
from strava_agent_sdk import StravaAgentClient

client = StravaAgentClient(
    gcp_project_id="strava-chat",
    gcp_credentials_path="./admin-key.json",
)
client.check_gcp_auth()

client.create_secrets_from_env(
    ".env",
    names=["PINECONE_API_KEY", "STRAVA_CLIENT_SECRET"],
    grant_access_to="serviceAccount:123-compute@developer.gserviceaccount.com",
)
```

### Ejemplo: cargar secretos al arrancar la app

```python
client = StravaAgentClient(gcp_project_id="strava-chat")
client.load_secrets_into_env([
    "STRAVA_CLIENT_SECRET",
    "STRAVA_OAUTH_STATE_SECRET",
    "PINECONE_API_KEY",
])
```

Util en Cloud Run: la SA del servicio autentica sola, los secretos se cargan como `os.environ` y el resto del codigo los lee como si vinieran de `.env`.

## Compatibilidad

`app.py` se mantiene como wrapper REST fino sobre el SDK para compatibilidad hacia atras.
