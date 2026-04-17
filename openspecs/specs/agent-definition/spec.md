# Spec: Agent Definition (v3 — Multi-Agent Patterns)

## Purpose

Permitir a cada atleta diseñar visualmente un sistema multi-agente usando los
patrones nativos de Google ADK:

- **Coordinator/Dispatcher** — un LlmAgent central delega a sub-agentes.
- **Sequential Pipeline** — SequentialAgent ejecuta sub-agentes en orden fijo.
- **Parallel Fan-Out** — ParallelAgent ejecuta sub-agentes concurrentemente.
- **Loop / Iterative Refinement** — LoopAgent repite sub-agentes hasta condición.
- **Hierarchical** — árboles multi-nivel combinando los patrones anteriores.

El usuario define agentes, sus relaciones (`sub_agents`) y tipo de composición
desde un panel visual. El backend traduce el TOML a un grafo ADK real.

## Scope

- In scope:
  - Schema TOML v3 con `[[agents]]`, `type`, `model`, `description`, `sub_agents`.
  - Almacenamiento en Firestore collection `agent_definition_file`.
  - Endpoints CRUD + validate (sin cambio de path).
  - Builder que compone agentes ADK reales (LlmAgent, SequentialAgent,
    ParallelAgent, LoopAgent) según la topología declarada.
  - UI visual: lista de agentes con selector de tipo, modelo, sub_agents y
    preview de topología.
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
type = "llm"
model = "openai/gpt-5-mini"
description = "Investiga datos de entrenamiento del atleta"
prompt = "Analiza los datos de entrenamiento recientes y extrae patrones."
sub_agents = []
order = 1

[[agents]]
id = "summarizer"
type = "llm"
model = "gemini/gemini-2.5-flash"
description = "Resume hallazgos de investigación"
prompt = "Resume los hallazgos del researcher en 3 puntos accionables."
sub_agents = []
order = 2

[[agents]]
id = "research_pipeline"
type = "sequential"
description = "Ejecuta researcher y luego summarizer en secuencia"
sub_agents = ["researcher", "summarizer"]
order = 3
```

### Campos permitidos

#### `system`

| Campo | Tipo | Requerido | Descripción |
|-------|------|-----------|-------------|
| `entrypoint` | string | sí | Debe ser `orchestrator` en v3 |

#### `agents`

| Campo | Tipo | Requerido | Descripción |
|-------|------|-----------|-------------|
| `id` | string | sí | snake_case, único, no reservado |
| `type` | string | sí | `llm` \| `sequential` \| `parallel` \| `loop` |
| `model` | string | no | LiteLLM model ID (solo para `type = "llm"`). Vacío = modelo de entorno |
| `description` | string | no | Descripción para delegación LLM (recomendado) |
| `prompt` | string | sí* | Instrucción del agente. *Requerido solo para `type = "llm"` |
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

## Builder Behaviour (v3)

`AgentDefinitionBuilder.build_orchestrator()`:

1. Cargar TOML del atleta (o template por defecto).
2. Parsear y validar schema v3.
3. Construir grafo de dependencias desde `sub_agents`.
4. Validar: sin ciclos, todas las refs existen, tipos correctos.
5. Construir agentes bottom-up (hojas primero):
   - `type = "llm"` → `LlmAgent(name=id, instruction=prompt, model=model)`
   - `type = "sequential"` → `SequentialAgent(name=id, sub_agents=[...])`
   - `type = "parallel"` → `ParallelAgent(name=id, sub_agents=[...])`
   - `type = "loop"` → `LoopAgent(name=id, sub_agents=[...])`
6. Agentes raíz (no referenciados como sub_agent por ningún otro custom) →
   `AgentTool` del orchestrator.
7. Agentes internos del sistema (intent_router, query_agent, etc.) → siempre
   como `AgentTool` del orchestrator.

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
   - Tipo (dropdown: llm, sequential, parallel, loop)
   - Modelo (dropdown, solo visible para type=llm)
   - Descripción (input text)
   - Prompt (textarea, solo visible para type=llm)
   - Sub-agents (checkboxes de agentes disponibles, excluye self + ancestros)
   - Output key (input text, opcional)

### Topology Preview

Debajo de la lista de agentes en sidebar, mini-visualización de la jerarquía
como árbol indentado con iconos de tipo:

```
🤖 researcher (llm)
🤖 summarizer (llm)
📋 research_pipeline (sequential)
  ├─ researcher
  └─ summarizer
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
- Google ADK 1.26+ (`LlmAgent`, `SequentialAgent`, `ParallelAgent`,
  `LoopAgent`, `AgentTool`)
