import json
import os
import secrets
from agent.models.self.strava_rl import StravaData


DEFAULT_REDIRECT_URI = 'http://localhost/exchange_token'
DEFAULT_SCOPE = 'profile:read_all,activity:read_all'


def _resolve_strava_client_id(client_id: int | None = None) -> int:
    if client_id is not None:
        return client_id

    raw_client_id = (os.getenv('STRAVA_CLIENT_ID') or '').strip()
    if not raw_client_id:
        raise ValueError('STRAVA_CLIENT_ID is not configured in environment')

    try:
        return int(raw_client_id)
    except ValueError as exc:
        raise ValueError('STRAVA_CLIENT_ID must be a valid integer') from exc


def _resolve_strava_client_secret(client_secret: str | None = None) -> str:
    if client_secret is not None:
        resolved_client_secret = client_secret.strip()
        if not resolved_client_secret:
            raise ValueError('client_secret cannot be empty')
        return resolved_client_secret

    resolved_client_secret = (os.getenv('STRAVA_CLIENT_SECRET') or '').strip()
    if not resolved_client_secret:
        raise ValueError('STRAVA_CLIENT_SECRET is not configured in environment')

    return resolved_client_secret


def start_strava_oauth(
    client_id: int | None = None,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
    scope: str = DEFAULT_SCOPE,
    state: str | None = None,
) -> str:
    """Prepare the Strava OAuth URL and human approval instructions."""
    resolved_client_id = _resolve_strava_client_id(client_id)

    if redirect_uri is None:
        redirect_uri = DEFAULT_REDIRECT_URI
    if scope is None:
        scope = DEFAULT_SCOPE

    if not isinstance(redirect_uri, str):
        raise ValueError('redirect_uri must be a string')
    if not isinstance(scope, str):
        raise ValueError('scope must be a string')

    redirect_uri = redirect_uri.strip() or DEFAULT_REDIRECT_URI
    scope = scope.strip() or DEFAULT_SCOPE

    generated_state = state or secrets.token_urlsafe(24)
    auth_url = StravaData.build_authorization_url(
        client_id=resolved_client_id,
        redirect_uri=redirect_uri,
        scope=scope,
        state=generated_state,
    )
    payload = {
        'status': 'authorization_required',
        'auth_url': auth_url,
        'redirect_uri': redirect_uri,
        'scope': scope,
        'state': generated_state,
        'instructions': [
            'Abre auth_url en el navegador.',
            'Pulsa Accept en la pantalla OAuth de Strava.',
            'Pega la URL completa de redirección para que el agente valide state y extraiga code automáticamente.',
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def parse_strava_redirect_url(redirected_url: str) -> str:
    """Extract the authorization code from the redirected URL."""
    authorization = StravaData.parse_authorization_response(redirected_url)
    payload = {
        'status': 'code_extracted',
        'code': authorization['code'],
        'state': authorization.get('state'),
        'scope': authorization.get('scope', []),
    }
    return json.dumps(payload, ensure_ascii=False)


def complete_strava_oauth(
    redirected_url: str,
    client_id: int | None = None,
    client_secret: str | None = None,
    expected_state: str | None = None,
) -> str:
    """Complete the HITL OAuth flow by validating the callback and exchanging the code for tokens."""
    resolved_client_id = _resolve_strava_client_id(client_id)
    resolved_client_secret = _resolve_strava_client_secret(client_secret)

    result = StravaData.complete_oauth(
        client_id=resolved_client_id,
        client_secret=resolved_client_secret,
        redirected_url=redirected_url,
        expected_state=expected_state,
    )
    payload = {
        'status': 'authenticated',
        'authorization': result['authorization'],
        'tokens': result['tokens'],
        'instructions': [
            'Guarda refresh_token de forma segura.',
            'Usa access_token para llamadas API hasta expires_at.',
            'Cuando expire, llama a refresh_strava_access_token y sustituye el refresh_token anterior por el nuevo.',
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def refresh_strava_access_token(
    refresh_token: str,
    client_id: int | None = None,
    client_secret: str | None = None,
) -> str:
    """Refresh the Strava access token and return the rotated refresh token."""
    resolved_client_id = _resolve_strava_client_id(client_id)
    resolved_client_secret = _resolve_strava_client_secret(client_secret)

    token_response = StravaData.refresh_access_token(
        client_id=resolved_client_id,
        client_secret=resolved_client_secret,
        refresh_token=refresh_token,
    )
    payload = {
        'status': 'token_refreshed',
        'tokens': StravaData.build_token_payload(token_response),
        'instructions': [
            'Sustituye el refresh_token anterior por el nuevo refresh_token.',
            'No reutilices el refresh_token previo porque Strava lo invalida tras el refresh.',
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def train_strava_rl_model(
    redirected_url: str,
    client_id: int | None = None,
    client_secret: str | None = None,
    save_path: str = 'cycling_ppo',
    total_timesteps: int = 100_000,
    prediction_steps: int = 7,
    render_mode: str = 'human',
) -> str:
    """Complete the OAuth-to-training Strava RL workflow after user approval."""
    resolved_client_id = _resolve_strava_client_id(client_id)
    resolved_client_secret = _resolve_strava_client_secret(client_secret)

    authorization = StravaData.parse_authorization_response(redirected_url)
    result = StravaData.run_training_pipeline(
        client_id=resolved_client_id,
        client_secret=resolved_client_secret,
        code=authorization['code'],
        save_path=save_path,
        total_timesteps=total_timesteps,
        prediction_steps=prediction_steps,
        render_mode=render_mode,
    )
    result['status'] = 'training_completed'
    result['redirected_url_received'] = True
    result['authorization'] = authorization
    return json.dumps(result, ensure_ascii=False)