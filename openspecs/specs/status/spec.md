# Spec: Status

## Purpose
Read-only queries over pipeline runs, known athletes, and indexing state. Used for observability and debugging of background jobs.

## Scope
- In scope:
  - List pipeline runs + stages
  - List athletes
  - Report indexing status per athlete
- Out of scope:
  - Mutating pipeline state
  - Metrics dashboards

## Source Anchors
- `strava_agent_sdk/services/status.py`

## Public API / Endpoints
<status query endpoints; enumerate once stabilized>

## Inputs & Outputs
<filters: athlete, run id, stage; response: status records>

## Dependencies
- Storage backend (run + index metadata)

## Behaviour
<happy path, missing athlete, stale runs, pagination>

## Open Questions
<TBD>
