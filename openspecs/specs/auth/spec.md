# Spec: Auth

## Purpose
Strava OAuth 2.0 flow — start authorization, exchange authorization code, refresh access tokens. Enables every downstream feature that calls the Strava API on behalf of an athlete.

## Scope
- In scope:
  - OAuth start redirect
  - Code → token exchange
  - Refresh token rotation
  - Token persistence per athlete
- Out of scope:
  - UI / consent screens
  - Non-Strava identity providers

## Source Anchors
- `strava_agent_sdk/services/auth.py`

## Public API / Endpoints
- `GET /auth/strava/start`
- `GET /auth/strava/exchange`
- `POST /auth/strava/refresh`

## Inputs & Outputs
<request/response shapes, token payloads, stored fields>

## Dependencies
- Strava OAuth endpoints
- Secret storage (client_id, client_secret)
- Token store (Firestore / storage backend)

## Behaviour
<happy path, expired token refresh, revoked scope, error responses>

## Open Questions
<TBD>
