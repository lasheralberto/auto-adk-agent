# Spec: Chat

## Purpose
Multi-model LLM chat layer. Supports response formats `plan_react_v1`, `structured`, and `plain`. Main user-facing interaction surface of the agent.

## Scope
- In scope:
  - Model routing via litellm
  - Response format selection
  - Agent runner orchestration
  - Tool-calling loop
  - Trazabilidad de ejecucion por conversacion (chain de agentes/eventos)
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
- `GET /agent-definition-logs/{athlete_id}?page={n}&page_size={n}&include_events={bool}`

## Inputs & Outputs
<request schema: messages, model, format; response schema per format>

## Dependencies
- litellm / OpenAI-compatible providers
- Agent tool registry
- Secrets for model API keys
- Firestore (`agent_definition_logs`) con fallback local JSON cuando Firestore no esta disponible

## Behaviour
### Happy path (chat no streaming)
1. `ChatService` prepara contexto RAG y construye el orchestrator.
2. `run_agent()` ejecuta intento 1 y, si no hay texto final, hace retry obligatorio (intento 2).
3. Se devuelve `response`, `tool_calls` y opcionalmente `structured`.

### Happy path (chat streaming)
1. `run_agent_streaming()` emite chunks SSE con `response` y `tool_calls` incrementales.
2. Si no se obtiene salida util en intento 1, ejecuta retry obligatorio.

### Conversation chain logging
En cada conversacion se persiste un trace resumido por atleta en:
- Collection: `agent_definition_logs`
- Document ID: `{athlete_id}`

### Read logs endpoint
- `GET /agent-definition-logs/{athlete_id}` devuelve conversaciones paginadas (ordenadas de mas reciente a mas antigua).
- Query params:
  - `page` (default `1`)
  - `page_size` (default `5`, max `50`)
  - `include_events` (default `false`; cuando es `true`, incluye `attempts[].events`)

Cada entrada de `conversations[]` incluye al menos:
- `conversation_id`, `created_at`, `stream`, `response_format`, `success`
- `agent_chain` (orden de agentes por donde paso la conversacion)
- `attempts[]` con `session_id`, `tool_calls`, `events` y `response_preview`

Si Firestore falla, el backend guarda el mismo payload en estado local temporal (`%TEMP%/strava_agent_state/agent_definition_logs.json`).

## Open Questions
<TBD>
