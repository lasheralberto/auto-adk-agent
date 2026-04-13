# Strava Agent SDK - Ejemplos Simples

Guia express para usar el SDK sin drama.

## 1) Arranque local con .env

Tu `.env`:

```dotenv
STRAVA_CLIENT_ID=...
STRAVA_CLIENT_SECRET=...
STRAVA_OAUTH_STATE_SECRET=...
STRAVA_ALLOWED_REDIRECT_URIS=https://miapp.com/auth/strava/callback
```

Tu script minimo:

```python
import asyncio
from dotenv import load_dotenv
from strava_agent_sdk import StravaAgentClient


async def main():
    load_dotenv(".env")

    client = StravaAgentClient(
        gcp_project_id="strava-chat",
        gcp_credentials_path="./strava-chat.json",
    )

    auth = await client.start_strava_oauth(
        redirect_uri="https://miapp.com/auth/strava/callback",
        scope="read,activity:read_all,profile:read_all",
    )
    print("Auth URL:", auth["auth_url"])


asyncio.run(main())
```

## 2) OAuth completo en modo corto

```python
tokens = await client.exchange_strava_code(
    code="CODE_FROM_CALLBACK",
    state="STATE_FROM_CALLBACK",
    redirect_uri="https://miapp.com/auth/strava/callback",
    scope="read,activity:read_all,profile:read_all",
)

tokens = await client.refresh_strava_token(
    refresh_token=tokens["refresh_token"],
    athlete_id=int(tokens.get("athlete", {}).get("id") or 0) or None,
)
```

## 3) Secret Manager wrapper: volcar .env al proyecto

Este es el flujo para subir variables desde archivo a Secret Manager usando el wrapper del SDK.

```python
from strava_agent_sdk import StravaAgentClient

client = StravaAgentClient(
    gcp_project_id="strava-chat",
    gcp_credentials_path="./admin-key.json",  # credenciales con permisos admin
)

client.check_gcp_auth()
```

### 3.1 Subir todo el .env

```python
client.create_secrets_from_env(".env")
```

### 3.2 Subir solo algunas variables

```python
client.create_secrets_from_env(
    ".env",
    names=["STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET"],
)
```

### 3.3 Solo actualizar secretos ya existentes

```python
client.create_secrets_from_env(
    ".env",
    only_update=True,
)
```

### 3.4 Dar acceso explicito a una SA concreta

```python
client.create_secrets_from_env(
    ".env",
    grant_access_to="serviceAccount:153952529856-compute@developer.gserviceaccount.com",
)
```

### 3.5 Gestion extra (por si hace falta)

```python
client.set_secret("FEATURE_FLAG", "on")
value = client.get_secret("FEATURE_FLAG")
all_names = client.list_secrets()
client.grant_secret_access(
    "FEATURE_FLAG",
    "serviceAccount:153952529856-compute@developer.gserviceaccount.com",
)
```

## 4) Chat rapido

```python
res = await client.chat(
    question="Como va mi semana de carga?",
    athlete_id=12345,
    model_name="gemini-2.5-flash",
)
print(res.response)
```

Listo. Cafe, deploy, y a pedalear.
