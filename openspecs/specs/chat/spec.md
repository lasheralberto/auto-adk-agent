# Spec: Chat

## Purpose
Multi-model LLM chat layer. Supports response formats `plan_react_v1`, `structured`, and `plain`. Main user-facing interaction surface of the agent.

## Scope
- In scope:
  - Model routing via litellm
  - Response format selection
  - Agent runner orchestration
  - Tool-calling loop
- Out of scope:
  - Wiki-specific chat (see `wiki-chat`)
  - Model training / fine-tuning

## Source Anchors
- `strava_agent_sdk/services/chat.py`
- `agent/runner.py`
- `agent/agents/wiki_research_chat_agent.py`

## Public API / Endpoints
- `POST /chat`
- `POST /ask`

## Inputs & Outputs
<request schema: messages, model, format; response schema per format>

## Dependencies
- litellm / OpenAI-compatible providers
- Agent tool registry
- Secrets for model API keys

## Behaviour
<happy path, tool-call loop, format validation, fallback models, error modes>

## Open Questions
<TBD>
