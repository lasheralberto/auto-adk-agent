# Spec: Wiki Research

## Purpose
Deep-research agent that analyzes ingested activities and generates activity-level insights via LLM. Produces wiki-page content consumed by indexing and wiki-chat.

## Scope
- In scope:
  - Per-activity research prompts
  - Multi-step LLM reasoning
  - Structured insight output
- Out of scope:
  - Vector indexing (see `wiki-vector-search`)
  - Conversational retrieval (see `wiki-chat`)

## Source Anchors
- `agent/tools/pipeline/research_wiki_agent.py`

## Public API / Endpoints
<invoked via pipeline research stage>

## Inputs & Outputs
<input: activity record; output: wiki page / insight document>

## Dependencies
- LLM provider (litellm)
- Storage backend for wiki pages
- Ingested activities from `activity-ingestion`

## Behaviour
<happy path, retry on LLM failure, output schema validation, cost budget>

## Open Questions
<TBD>
