# Spec: Activity Ingestion

## Purpose
Fetch athlete activities from Strava API and persist them into the storage backend. Feeds all downstream features (research, indexing, RL model).

## Scope
- In scope:
  - Strava API pagination + rate limiting
  - Activity normalization
  - Storage write path
- Out of scope:
  - Real-time webhook processing (unless explicitly added)
  - Analysis / insight generation

## Source Anchors
- `agent/tools/pipeline/connectors/strava.py`
- `agent/tools/pipeline/storage_backend.py`

## Public API / Endpoints
<invoked via pipeline stage; no direct HTTP endpoint>

## Inputs & Outputs
<athlete id, date range; output: stored activity records>

## Dependencies
- Strava API (OAuth token from `auth`)
- Storage backend (Firestore / GCS)
- Rate-limit config

## Behaviour
<happy path, rate-limit backoff, incremental vs full sync, dedup>

## Open Questions
<TBD>
