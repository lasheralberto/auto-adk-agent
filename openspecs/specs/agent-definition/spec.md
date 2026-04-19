# Spec: Agent Definition (v3.1 — Consensus Loop Runtime)

## Purpose

Permitir a cada atleta diseñar visualmente un sistema multi-agente con una
interaccion util entre agentes: iteracion por rondas, intercambio por
`output_key` y sintesis final de consenso.

El schema TOML mantiene compatibilidad con tipos ADK, pero el runtime de chat
usa un modo determinista de consenso sobre los agentes definidos por el usuario.

- **Coordinator/Dispatcher** — un LlmAgent central delega a sub-agentes.
- **Sequential Pipeline** — SequentialAgent ejecuta sub-agentes en orden fijo.
- **Parallel Fan-Out** — ParallelAgent ejecuta sub-agentes concurrentemente.
- **Loop / Iterative Refinement** — LoopAgent repite sub-agentes hasta condición.
- **Hierarchical** — árboles multi-nivel combinando los patrones anteriores.

El usuario define agentes (nombre, prompt, modelo, output_key y orden) desde un
panel visual. El backend traduce el TOML a un pipeline de consenso ejecutable.

## Scope

- In scope:
  - Schema TOML v3 con `[[agents]]`, `type`, `model`, `description`, `sub_agents`.
  - Compatibilidad de lectura con tablas nombradas: `[agents.<id>]` y `[workflow.<id>]`.
  - `type = "custom"` para agentes registrados en backend via factory.
  - Almacenamiento en Firestore collection `agent_definition_file`.
  - Endpoints CRUD + validate (sin cambio de path).
  - Builder que compone un consenso determinista: rondas de agentes +
    sintetizador final (`SequentialAgent`).
  - UI visual simplificada: lista de agentes, editor llm-only y preview del
    ciclo de iteracion + nodo de consenso.
  - Canvas visual de solo lectura para inspeccionar el flujo de consenso.
  - Validación: ciclos, referencias válidas, tipos correctos.
  - Migración automática de v2 `[[prompt_agents]]` → v3 `[[agents]]`.
- Out of scope:
  - Configuración de tools por usuario.
  - Configuración de skills por usuario.
  - React Flow / drag-and-drop graph editor (futuro).
  - Custom BaseAgent code desde UI.

## Source Anchors

- `agent/builder.py` — parseo, validación, compose runtime.
- `agent/app.py` — `build_orchestrator()` delegado al builder.
- `agent/runner.py` — ejecución (sin cambios).
- `strava_agent_sdk/flask/routes.py` — endpoints `/agent-definition/*`.
- `strava-agent-front/src/components/ui/customizable-agents-panel.tsx` — editor visual.

## TOML Schema (v3)

```toml
[system]
entrypoint = "orchestrator"

[[agents]]
id = "researcher"
name = "Researcher"
type = "llm"
model = "openai/gpt-5-mini"
description = "Investiga datos de entrenamiento del atleta"
prompt = "Analiza los datos de entrenamiento recientes y extrae patrones."
sub_agents = []
order = 1

[[agents]]
id = "summarizer"
name = "Summarizer"
type = "llm"
model = "gemini/gemini-2.5-flash"
description = "Resume hallazgos de investigación"
prompt = "Resume los hallazgos del researcher en 3 puntos accionables."
sub_agents = []
order = 2

[[agents]]
id = "research_pipeline"
name = "Research Pipeline"
type = "sequential"
description = "Ejecuta researcher y luego summarizer en secuencia"
sub_agents = ["researcher", "summarizer"]
order = 3
```

### Compatibilidad de entrada (normalizada a v3)

También se acepta el formato por tablas nombradas para facilitar edición manual
de workflows:

```toml
[system]
entrypoint = "orchestrator"

[agents.researcher]
name = "Researcher"
type = "LlmAgent"
model = "openai/gpt-5-mini"
instructions = "Analiza los datos recientes."
output_key = "research_result"

[workflow.pipeline]
name = "Pipeline"
type = "SequentialAgent"
sub_agents = ["researcher", "summarizer"]
```

Durante lectura:
- `LlmAgent` / `SequentialAgent` / `ParallelAgent` / `LoopAgent` se mapean a
  `llm` / `sequential` / `parallel` / `loop`.
- `instruction` o `instructions` se mapean a `prompt`.
- En el primer `PUT` exitoso, se persiste en formato canónico `[[agents]]`.

### Campos permitidos

#### `system`

| Campo | Tipo | Requerido | Descripción |
|-------|------|-----------|-------------|
| `entrypoint` | string | sí | Debe ser `orchestrator` en v3 |

#### `agents`

| Campo | Tipo | Requerido | Descripción |
|-------|------|-----------|-------------|
| `id` | string | sí | snake_case, único, no reservado. Auto-generado desde `name`. |
| `name` | string | sí | Nombre visible del agente. El `id` se deriva normalizando este campo. |
| `type` | string | sí | `llm` \| `sequential` \| `parallel` \| `loop` (compatibilidad de schema; runtime usa consenso llm-only) |
| `model` | string | no | LiteLLM model ID (solo para `type = "llm"`). Vacío = modelo de entorno |
| `description` | string | no | Descripción para delegación LLM (recomendado) |
| `prompt` | string | sí* | Instrucción del agente. *Requerido solo para `type = "llm"` |
| `custom_type` | string | sí* | Identificador de factory registrada. *Requerido para `type = "custom"` (o inferido desde `name`) |
| `sub_agents` | string[] | sí* | IDs de agentes hijos. *Requerido y no vacío para `sequential`, `parallel`, `loop` |
| `output_key` | string | no | Clave de estado donde el agente guarda su output (para pipelines) |
| `order` | int | no | Prioridad visual / de evaluación |

### Tipos de agente

| Tipo | ADK Class | Campos requeridos | Semántica |
|------|-----------|-------------------|-----------|
| `llm` | `LlmAgent` | `prompt` | Agente con LLM que sigue instrucciones. Puede tener `sub_agents` opcionales (delegación). |
| `sequential` | `SequentialAgent` | `sub_agents` (≥1) | Ejecuta hijos en orden. Output del anterior disponible vía `output_key`. |
| `parallel` | `ParallelAgent` | `sub_agents` (≥1) | Ejecuta hijos concurrentemente. Resultados en estado compartido. |
| `loop` | `LoopAgent` | `sub_agents` (≥1) | Itera sobre hijos hasta `max_iterations` o escalación. |
| `custom` | Factory registrada | `custom_type` \/ `name` | Instancia un agente Python custom registrado en backend. |

> Nota runtime: en chat, los agentes definidos por usuario se ejecutan en modo
> consenso llm-only por rondas. `type` y `sub_agents` se mantienen para
> compatibilidad de lectura/validacion del TOML.

### Campos NO permitidos

- `tools` — no user-configurable tools
- `skill` — no skill configuration
- `planner` por agente — planner es interno
- `wiki_context` — no per-agent wiki config
- `instruction` (legacy) — usar `prompt`

## Data Model

**Collection:** `agent_definition_file`

**Document ID:** `{athlete_id}`

```text
AgentDefinitionDoc {
  athlete_id: string
  toml_content: string
  version: int
  updated_at: string
  updated_by: string | null

  // Denormalizados
  entrypoint: string
  agent_count: int
  agent_ids: string[]
}
```

## Public API / Endpoints

Sin cambio de path:

- `GET /agent-definition/{athlete_id}`
- `PUT /agent-definition/{athlete_id}`
- `DELETE /agent-definition/{athlete_id}`
- `POST /agent-definition/{athlete_id}/validate`

Headers:

- `X-Internal-Token` requerido.

Body de `PUT`:

```json
{
  "toml_content": "...",
  "version": 3
}
```

## Builder Behaviour (v3.1)

`AgentDefinitionBuilder.build_orchestrator()`:

1. Cargar TOML del atleta (o template por defecto).
2. Parsear y validar schema v3.
3. Cargar agentes definidos por usuario, ordenados por `order`.
4. Resolver `output_key` por agente (si falta, fallback `{agent_id}_output`).
5. Construir pipeline de consenso determinista:
  - Rondas fijas de iteracion (actualmente 2).
  - En cada ronda, cada agente se ejecuta como `LlmAgent` y escribe su
    resultado en su `output_key`.
  - Al final, un `consensus_finalizer` sintetiza una respuesta unica.
6. Exponer ese pipeline como un solo `AgentTool` (`consensus_loop_pipeline`)
  para el orchestrator.
7. Agentes internos del sistema (intent_router, query_agent, etc.) se
  mantienen como `AgentTool` del orchestrator.

### Compatibilidad de tipos en runtime

- El TOML sigue aceptando `type` y `sub_agents` por compatibilidad.
- En runtime de chat, los agentes definidos por usuario se ejecutan en modo
  consenso (llm-only) independientemente del `type` declarado.

### Resolución de modelo

Para `type = "llm"`:
- Si `model` está definido → usar ese modelo via `get_llm_provider(model)`.
- Si vacío → usar modelo de entorno (AGENT_LLM_MODEL).

Para workflow agents (sequential, parallel, loop):
- No usan modelo directamente (delegan a sub_agents).

## Frontend / Visual Editor

### Layout

Panel lateral derecho (drawer) con:

1. **Sidebar** (280px): lista de agentes con badges de tipo e icono.
2. **Editor** (resto): propiedades del agente seleccionado:
   - ID (solo lectura)
  - Modelo (dropdown)
   - Descripción (input text)
  - Prompt (textarea)
   - Output key (input text, opcional)
  - Orden de ejecucion (botones arriba/abajo en sidebar)

### Topology Preview

Debajo de la lista de agentes, se visualiza un ciclo de iteracion automatico:

```
agent_1 -> agent_2 -> ... -> agent_n -> agent_1
agent_i --(output_key)--> consenso_final
```

## Validation Rules (v3)

| Regla | Error |
|-------|-------|
| TOML inválido | `Invalid TOML syntax: ...` |
| Falta `system.entrypoint` | `Field 'system.entrypoint' is required.` |
| `entrypoint != orchestrator` | `Entrypoint must be 'orchestrator'.` |
| Sin agentes | `At least one [[agents]] entry is required.` |
| Exceso de agentes (>10) | `Definition exceeds max agents.` |
| `type` inválido | `Agent '{id}': invalid type '{type}'.` |
| `type=llm` sin prompt | `Agent '{id}': 'prompt' is required for type 'llm'.` |
| Workflow sin sub_agents | `Agent '{id}': 'sub_agents' required for type '{type}'.` |
| sub_agent ref inválida | `Agent '{id}': sub_agent '{ref}' does not exist.` |
| Ciclo en sub_agents | `Circular dependency detected: {path}.` |
| ID duplicado | `Duplicate agent id '{id}'.` |
| ID reservado | `Agent id '{id}' is reserved.` |
| Campo no permitido | `Field 'agents[{n}].{field}' is not allowed.` |

## Migration from v2 (prompt-only)

Backward compatibility automática:

1. Si se detecta `[[prompt_agents]]` al leer:
   - Convertir cada entry a `[[agents]]` con `type = "llm"`, `sub_agents = []`.
   - `prompt` se mantiene, `model = ""`, `description = ""`.
2. Si se detecta legacy `[[agents]]` con `instruction`:
   - Mapear `instruction` → `prompt`, agregar `type = "llm"`.
3. En el primer `PUT` exitoso, persistir en formato v3.

## Error Modes

| Escenario | HTTP | Detalle |
|-----------|------|---------|
| TOML inválido | 400 | Parse/validation error |
| Validación semántica falla | 400 | Lista de errores |
| Version conflict | 409 | Optimistic locking |
| Unauthorized | 401 | Token interno inválido/faltante |
| Error interno | 500 | Falla no controlada |

## Dependencies

- `tomllib` / `tomli`
- Firestore (`firebase_admin`)
- Google ADK 1.26+ (`LlmAgent`, `SequentialAgent`, `AgentTool`)

### Registro de CustomAgent (backend)

El backend expone registro dinámico de factories en `agent.builder`:

- `register_custom_agent(custom_type, factory)`
- `unregister_custom_agent(custom_type)`
- `list_registered_custom_agents()`

El `factory` recibe `(config, sub_agents)` y debe devolver una instancia de
agente ADK válida para ser envuelta por `AgentTool` en el orchestrator.
