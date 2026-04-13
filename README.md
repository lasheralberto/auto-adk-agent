# Strava Agent SDK - Script unico y simple

Un solo script. Tres pasos. Cero vueltas:

1. Autenticarnos con Google Cloud y cargar `.env`.
2. Hacer el auth flow de Strava (start + callback + refresh).
3. Lanzar chat con el atleta autenticado.

Tu `.env`:

```dotenv
STRAVA_CLIENT_ID=...
STRAVA_CLIENT_SECRET=...
STRAVA_OAUTH_STATE_SECRET=...
STRAVA_ALLOWED_REDIRECT_URIS=https://miapp.com/auth/strava/callback
```

Script unico (copy/paste):

```python
import asyncio
import time
from dotenv import load_dotenv
from strava_agent_sdk import StravaAgentClient

PROJECT_ID = "strava-chat"
RUNTIME_KEY_PATH = "./strava-chat.json"
ADMIN_KEY_PATH = "./admin-key.json"  # opcional, solo para bootstrap de secrets
REDIRECT_URI = "https://miapp.com/auth/strava/callback"
SCOPE = "read,activity:read_all,profile:read_all"


async def main():
    # 1) Google Cloud + .env
    load_dotenv(".env")

    client = StravaAgentClient(
        gcp_project_id=PROJECT_ID,
        gcp_credentials_path=RUNTIME_KEY_PATH,
    )
    print("GCP auth:", client.check_gcp_auth())

    # Opcional: subir .env a Secret Manager con credenciales admin
    # admin_client = StravaAgentClient(
    #     gcp_project_id=PROJECT_ID,
    #     gcp_credentials_path=ADMIN_KEY_PATH,
    # )
    # admin_client.create_secrets_from_env(".env")

    # 2) Strava auth flow
    start = await client.start_strava_oauth(
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
    )
    print("\nAbre esta URL y autoriza en Strava:")
    print(start["auth_url"])
    print("\nCuando Strava te redirija a tu callback, pega los valores aca.")

    callback_code = input("code: ").strip()
    callback_state = input("state: ").strip() or start["state"]

    tokens = await client.exchange_strava_code(
        code=callback_code,
        state=callback_state,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
    )

    athlete_id = int(tokens.get("athlete", {}).get("id") or 0)
    expires_at = int(tokens.get("expires_at") or 0)
    now = int(time.time())

    if expires_at <= now + 60:
        tokens = await client.refresh_strava_token(
            refresh_token=tokens["refresh_token"],
            athlete_id=athlete_id or None,
        )

    access_token = tokens.get("access_token")
    if not athlete_id or not access_token:
        raise RuntimeError("No se pudo obtener athlete_id/access_token.")

    # 3) Chat
    res = await client.chat(
        question="Como va mi semana de carga?",
        athlete_id=athlete_id,
        access_token=access_token,
        model_name="gemini-2.5-flash",
    )
    print("\nRespuesta del agente:\n")
    print(res.response)


asyncio.run(main())
```

Listo. Un script, todo el flujo, y a pedalear.
