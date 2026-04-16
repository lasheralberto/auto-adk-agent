# Spec: Pipeline

## Purpose
Async data-processing orchestration. Coordinates stages: daily scheduling, ingestion, research, indexing. Entry point for background workflows triggered by cron or manual calls.

## Scope
- In scope:
  - Workflow stage sequencing
  - Per-athlete run tracking
  - Trigger endpoints (token-gated)
- Out of scope:
  - Individual stage logic (delegated to ingestion / research / indexing features)

## Source Anchors
- `strava_agent_sdk/services/pipeline.py`
- `agent/tools/pipeline/workflow.py`

## Public API / Endpoints
- `POST /pipeline/*` (token-gated internal triggers)
- `POST /pipeline/query`

## Inputs & Outputs
<request payloads per stage, run id, status response>

## Dependencies
- Storage backend (run state)
- Activity-ingestion, wiki-research, wiki-vector-search features
- Internal auth token

## Behaviour
<happy path, retries, partial failure, idempotency per athlete/run>

## Open Questions
<TBD>
