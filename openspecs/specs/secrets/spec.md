# Spec: Secrets

## Purpose
Abstraction over GCP Secret Manager for secure retrieval of credentials (Strava OAuth, LLM API keys, Pinecone key, etc.). Single access point so other features never hardcode secret lookup.

## Scope
- In scope:
  - Fetch secret by name
  - Cache / version pinning
  - Local dev fallback to env vars
- Out of scope:
  - Secret rotation automation
  - Access auditing UI

## Source Anchors
- `strava_agent_sdk/services/secrets.py`
- `scripts/sync_secrets.py`

## Public API / Endpoints
<internal SDK helper; no HTTP endpoint>

## Inputs & Outputs
<input: secret name, optional version; output: secret string>

## Dependencies
- `google-cloud-secret-manager`
- GCP service account / ADC
- Env var fallback

## Behaviour
<happy path, missing secret, permission denied, local dev mode>

## Open Questions
<TBD>
