# Spec: Dynamic Agents

## Purpose
CRUD para gestionar agentes de conversación definidos en runtime. Permite crear, listar, editar y eliminar agentes sin necesidad de desplegar código. Cada agente es un `instruction_template` con instrucciones puras — el contexto de la wiki y el ID del atleta se inyectan automáticamente por el backend.

## Scope
- In scope:
  - Creación, lectura, actualización y eliminación de agentes
  - Límite máximo de 5 agentes simultáneos (`MAX_AGENTS`)
  - Persistencia en Firestore (colección `agents`) con fallback local JSON
  - Protección de agentes por defecto (no se pueden eliminar)
  - Selección dinámica de agente en tiempo de chat
- Out of scope:
  - Lógica de ejecución del agente (ver `wiki-chat`, `chat`)
  - Vectorización o indexación de contenido wiki (ver `wiki-vector-search`)
  - Autenticación de usuarios finales (ver `auth`)

## Source Anchors
- `agent/agents/agent_prompts.py` — `AgentPromptStore` (CRUD store, Firestore + local fallback)
- `strava_agent_sdk/services/agents.py` — `AgentsService` (capa de servicio async)
- `strava_agent_sdk/client.py` — métodos `create_agent`, `update_agent`, `delete_agent`, `list_agents`, `get_agent`
- `strava_agent_sdk/flask/routes.py` — endpoints HTTP

## Data Model

```
AgentRecord {
  agent_id: string          // identificador único, snake_case
  name: string              // nombre visible en UI
  description: string       // descripción breve
  instruction_template: str // instrucciones del agente (sin placeholders — wiki y athlete_id se inyectan por backend)
  is_default: bool          // true si proviene de DEFAULT_TEMPLATES (inmutable/no eliminable)
  updated_at: string | null // ISO 8601 UTC
  updated_by: string | null // quién hizo el último cambio
}
```

### Storage
- **Primario**: Firestore, colección configurable via `FIRESTORE_AGENTS_COLLECTION` (default: `agents`)
- **Fallback**: JSON local en `$TMPDIR/strava_agent_state/agent_prompts.json`
- Los agentes en `DEFAULT_TEMPLATES` (código) siempre existen como base; los custom se almacenan en Firestore/local

## Public API / Endpoints

Todos los endpoints requieren header `X-Internal-Token` para autorización.

### `GET /agents`
Lista todos los agentes (defaults + custom desde Firestore).

**Response 200:**
```json
{
  "agents": [AgentRecord, ...]
}
```

### `GET /agents/<agent_id>`
Obtiene un agente por ID.

**Response 200:** `AgentRecord`
**Response 404:** agente no encontrado

### `POST /agents`
Crea un nuevo agente custom.

**Request body:**
```json
{
  "agent_id": "mi_agente",
  "name": "Mi Agente Custom",
  "description": "Descripción opcional",
  "instruction_template": "Eres un asistente que..."
}
```

> **Nota:** No se necesitan placeholders. El backend inyecta automáticamente el contexto de la wiki del atleta y su ID antes de ejecutar el agente.

**Response 201:** `AgentRecord` creado
**Response 400:**
- `agent_id` vacío o ya existe
- `name` vacío
- `instruction_template` vacío
- Límite de 5 agentes alcanzado

### `PUT /agents/<agent_id>`
Actualiza un agente existente (default o custom).

**Request body:**
```json
{
  "instruction_template": "Nuevo prompt...",
  "name": "Nombre actualizado (opcional)",
  "description": "Nueva descripción (opcional)"
}
```

**Response 200:** `AgentRecord` actualizado
**Response 400:** template vacío
**Response 404:** agente no encontrado

### `DELETE /agents/<agent_id>`
Elimina un agente custom. No permite eliminar agentes por defecto.

**Response 200:** `{ "deleted": true, "agent_id": "..." }`
**Response 400:** intento de eliminar agente default, o agente no encontrado

## Behaviour

### Happy path — Crear agente
1. Frontend envía `POST /agents` con `agent_id`, `name`, `instruction_template`
2. `AgentPromptStore.create()` valida unicidad y límite MAX_AGENTS
3. Documento se escribe en Firestore
4. Se devuelve el `AgentRecord` completo con `is_default: false`

### Happy path — Usar agente en chat
1. Frontend envía `POST /chat/wiki` con `agent_id` en el body
2. `WikiChatService._prepare_wiki_agent()` resuelve `agent_id` (default: `wiki_research_chat`)
3. `AgentPromptStore.get_template(agent_id)` carga el template desde Firestore
4. `build_wiki_research_chat_agent()` inyecta automáticamente `WIKI_CONTEXT_BLOCK` (wiki del atleta + athlete_id) como prefijo del template
5. `LlmAgent` se construye con el prompt completo (contexto wiki + instrucciones del agente)

### Protección de defaults
- `DEFAULT_TEMPLATES` define agentes base (actualmente solo `wiki_research_chat`)
- Se pueden **editar** (el template custom se guarda en Firestore, `is_default` pasa a `false`)
- No se pueden **eliminar** — `delete()` lanza `ValueError`

### Límite de agentes
- `MAX_AGENTS = 5` (incluye defaults + custom)
- `create()` cuenta `list_all()` antes de insertar
- Si se alcanza el límite, devuelve error 400

### Fallback Firestore → local
- Si Firestore no está disponible (sin credenciales, error de conexión), todas las operaciones caen al JSON local
- Transparente para la API — misma interfaz

## Error Modes
| Escenario | HTTP | Mensaje |
|-----------|------|---------|
| agent_id ya existe | 400 | `Agent 'X' already exists.` |
| Límite alcanzado | 400 | `Maximum number of agents (5) reached.` |
| Eliminar default | 400 | `Cannot delete default agent 'X'.` |
| agent_id no encontrado | 404 | `Agent 'X' not found.` |
| Template vacío | 400 | `instruction_template must be a non-empty string.` |
| Sin autorización | 401 | `Unauthorized.` |

## Frontend Integration

### Componente: `AgentPromptPanel`
- **Props**: `selectedAgentId`, `onAgentChange(agentId)`
- Al abrir el panel: `GET /agents` carga la lista completa
- Dropdown selector para cambiar entre agentes
- Botón "Nuevo" abre modal de creación (ID, nombre, descripción, prompt)
- Botón "Eliminar" visible solo para agentes custom
- Muestra contador `N/5`

### Chat request
- `POST /chat/wiki` ahora incluye campo `agent_id` en el body
- Si no se envía, el backend usa `wiki_research_chat` como fallback

## Context Injection

Los placeholders `%%ATHLETE_ID%%` y `%%WIKI%%` **no son responsabilidad del usuario**. El backend los inyecta siempre de forma automática:

1. `WIKI_CONTEXT_BLOCK` (definido en `agent_prompts.py`) contiene el bloque de contexto con `%%ATHLETE_ID%%` y `%%WIKI%%`
2. `build_wiki_research_chat_agent()` renderiza este bloque con los valores reales (athlete_id, wiki content)
3. El resultado se **antepone** al `instruction_template` del agente
4. El usuario solo escribe instrucciones puras en el template — nunca placeholders

## Dependencies
- Firestore (firebase_admin) — storage primario
- `agent/agents/agent_prompts.py` — `DEFAULT_TEMPLATES`, `WIKI_CONTEXT_BLOCK`, `render_template()`
- `strava_agent_sdk/errors.py` — `ValidationError`, `NotFoundError`

## Open Questions
- Permitir asociar herramientas (tools) específicas por agente?
- Versionado de templates (historial de cambios)?
