# Spec: Wiki Chat

## Purpose
Chat interface over an athlete's indexed wiki pages. Retrieves relevant insights via vector search and answers athlete-specific questions grounded in their own data. Supports dynamic agent selection at runtime.

## Scope
- In scope:
  - Retrieval-augmented chat
  - Per-athlete knowledge isolation
  - Citation of source pages
  - Dynamic agent selection via `agent_id` parameter
- Out of scope:
  - General-purpose chat (see `chat`)
  - Index maintenance (see `wiki-vector-search`)
  - Agent CRUD (see `agents`)

## Source Anchors
- `strava_agent_sdk/services/wiki_chat.py`
- `agent/agents/wiki_research_chat_agent.py`
- `agent/agents/agent_prompts.py`
- `agent/tools/pipeline/wiki_pages.py`

## Public API / Endpoints
- `POST /chat/wiki`

## Inputs & Outputs

### Request
```json
{
  "message": "¿Cómo va mi fatiga esta semana?",
  "athlete_id": 12345,
  "stream": true,
  "model": "gemini-2.5-flash (opcional)",
  "agent_id": "wiki_research_chat (opcional, default)"
}
```

- `message` / `question`: pregunta del usuario (requerido)
- `athlete_id`: ID del atleta en Strava (requerido)
- `stream`: si true, respuesta SSE chunked
- `model`: override del modelo LLM (opcional)
- `agent_id`: ID del agente a usar (opcional, default: `wiki_research_chat`). Debe existir en la colección `agents` (ver spec `agents`)

### Response
```json
{
  "response": "Según tu wiki, la fatiga...",
  "athlete_id": 12345,
  "tool_calls": []
}
```

## Dependencies
- `wiki-vector-search` for retrieval (Pinecone RAG)
- `agents` for agent template resolution
- LLM provider (configurable via `get_llm_provider`)
- Wiki page store (GCS)
- Firestore trace logs en `agent_definition_logs/{athlete_id}` (via `agent/runner.py`)

## Behaviour

### Happy path
1. Request llega con `message`, `athlete_id`, y opcionalmente `agent_id`
2. `WikiChatService._prepare_wiki_agent()` resuelve el `agent_id` (fallback: `wiki_research_chat`)
3. `read_wiki_content()` recupera páginas wiki relevantes vía RAG (Pinecone) o fallback full-wiki
4. `AgentPromptStore.get_template(agent_id)` carga el `instruction_template` del agente seleccionado
5. `build_wiki_research_chat_agent()` inyecta automáticamente `WIKI_CONTEXT_BLOCK` (wiki + athlete_id) como prefijo del template
6. `LlmAgent` se construye con el prompt completo (contexto wiki + instrucciones del agente)
7. `run_agent()` o `run_agent_streaming()` ejecuta y devuelve respuesta
8. El runner persiste la traza de la conversacion (chain de agentes, eventos y tool calls) en `agent_definition_logs/{athlete_id}`

### Agent not found
Si `agent_id` no existe en Firestore ni en `DEFAULT_TEMPLATES`, `get_template()` lanza `ValueError` → HTTP 500.

### Wiki not found
Si el atleta no tiene páginas wiki → HTTP 404 con `error: "wiki_not_found"`.

### Streaming
Con `stream: true`, la respuesta se envía como SSE (`text/event-stream`) con chunks incrementales.

## Error Modes
| Escenario | HTTP | Error |
|-----------|------|-------|
| message vacío | 400 | `Field 'message' or 'question' must be a non-empty string.` |
| athlete_id inválido | 400 | `Field 'athlete_id' is required.` |
| Wiki no encontrada | 404 | `wiki_not_found` |
| Agent template error | 500 | `Wiki chat agent setup failed.` |

## Open Questions
- Rate limiting por atleta/sesión?
- Historial de conversación (multi-turn)?
