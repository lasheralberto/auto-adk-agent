# Spec: RL Model

## Purpose
Reinforcement-learning model for athlete load / recovery estimation. Consumes ingested activities and produces predictions used by the agent for training recommendations.

## Scope
- In scope:
  - Model definition + inference path
  - Feature extraction from activities
- Out of scope:
  - Training infrastructure (offline)
  - Deployment / serving endpoint (unless added)

## Source Anchors
- `agent/models/self/strava_rl.py`

## Public API / Endpoints
<invoked via agent tools; no direct HTTP endpoint>

## Inputs & Outputs
<input: athlete activity history; output: load / recovery prediction>

## Dependencies
- Ingested activities from `activity-ingestion`
- Model artifacts (weights, config)

## Behaviour
<happy path, cold-start athlete with little history, model versioning>

## Open Questions
<TBD>
