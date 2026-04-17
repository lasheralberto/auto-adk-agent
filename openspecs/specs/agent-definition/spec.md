# Spec: Agent Definition (Prompt-Only UI)

## Purpose

Permitir a cada atleta crear agentes personalizados con la interfaz mas simple posible:

- solo prompt por agente nuevo
- sin configuracion de tools
- sin configuracion de skill
- sin topologia manual (sin edges/sub_agents en UI)
- sin formularios avanzados por agente

El sistema mantiene el runtime multi-agente, pero la complejidad queda en el backend. El usuario solo escribe prompts.

## Scope

- In scope:
  - Schema TOML simplificado para agentes prompt-only.
  - Almacenamiento en Firestore collection `agent_definition_file` (doc.id = athlete_id).
  - Endpoints CRUD + validate para definition file (se mantienen).
  - Builder que compone:
    - base orchestration interna (fija)
    - agentes custom creados desde prompts
  - UI de creacion/edicion en formato lista simple (sin React Flow obligatorio).
  - Validacion semantica y optimistic locking por version.
- Out of scope:
  - Configuracion de tools por usuario.
  - Configuracion de skill por usuario.
  - Configuracion de `sub_agents` por usuario.
  - Configuracion de planner por agente.
  - Overrides de modelo por agente.
  - Registro dinamico de tools Python desde TOML.

## Source Anchors

- `agent/builder.py` — parseo, validacion, compose runtime.
- `agent/app.py` — `build_orchestrator()` delegado al builder.
- `agent/runner.py` — ejecucion (sin cambios estructurales).
- `strava_agent_sdk/flask/routes.py` — endpoints `/agent-definition/*`.
- `strava-agent-front/src/components/ui/agent-designer-panel.tsx` — simplificar UX a prompt-only.

## UX Contract (Simplified)

La interfaz de creacion de agentes debe exponer solamente:

1. boton `Nuevo agente`
2. un `textarea` de prompt obligatorio
3. acciones basicas por item: editar prompt, eliminar

No debe exponer:

- selector de tools
- selector de skill/source
- planner checkbox
- wiki_context checkbox
- editor de conexiones/edges
- model override por agente

Opcional UX (no bloqueante):

- reordenar items por drag/drop para prioridad de delegacion

## TOML Schema (v2, prompt-only)

```toml
[system]
entrypoint = "orchestrator"
model = ""
planner_mode = "full_only"

[[prompt_agents]]
id = "agent_1"
prompt = """
Analiza la semana del atleta y devuelve 3 hallazgos accionables.
"""
order = 1

[[prompt_agents]]
id = "agent_2"
prompt = """
Explica riesgo de fatiga con base en tendencias recientes.
"""
order = 2
```

### Campos permitidos

#### `system`

| Campo | Tipo | Requerido | Descripcion |
|-------|------|-----------|-------------|
| `entrypoint` | string | si | Debe ser `orchestrator` en v2 |
| `model` | string | no | `""` = modelo de entorno |
| `planner_mode` | string | no | `always` \| `full_only` \| `off` |

#### `prompt_agents`

| Campo | Tipo | Requerido | Descripcion |
|-------|------|-----------|-------------|
| `id` | string | si | ID estable (auto-generado, no editable en UI) |
| `prompt` | string | si | Unico campo funcional editable por usuario |
| `order` | int | no | Prioridad de evaluacion/delegacion |

### Campos no permitidos en v2

Se consideran invalidados para la interfaz simplificada:

- `tools`
- `skill`
- `instruction` dentro de `[[agents]]`
- `sub_agents`
- `planner` por agente
- `wiki_context` por agente
- `model` por agente

La UI no debe generarlos y la validacion debe rechazarlos en payloads nuevos.

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
  prompt_agent_count: int
  prompt_agent_ids: string[]
}
```

## Public API / Endpoints

Se mantienen sin cambio de path:

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

## Builder Behaviour

`AgentDefinitionBuilder` mantiene la responsabilidad de construir el agente raiz, pero con semantica v2:

1. Cargar TOML del atleta (o template por defecto).
2. Parsear y validar schema prompt-only.
3. Construir N agentes `LlmAgent` desde `[[prompt_agents]]`:
   - `instruction = prompt`
   - sin tools configuradas por usuario
4. Inyectar esos agentes como capacidad delegable del `orchestrator` interno.
5. Devolver `orchestrator` como `system.entrypoint`.

Nota:

- Internamente ADK puede seguir usando `AgentTool` para wrapping entre agentes.
- "Sin tools" significa sin tools configurables por usuario, no eliminar la mecanica interna del runtime.

## Frontend Integration

`AgentDesignerPanel` migra de editor grafo a editor lista:

1. Load: `GET /agent-definition/{athlete_id}`.
2. Render: lista de prompts.
3. Crear: agrega nuevo item con prompt vacio.
4. Editar: solo textarea prompt.
5. Borrar: remove item.
6. Guardar: serializa TOML v2 y llama `PUT`.
7. Validar: llama `POST /validate` antes de guardar.

Estado minimo por item UI:

- `id` (solo lectura)
- `prompt` (editable)

## Validation Rules (v2)

| Regla | Error esperado |
|-------|----------------|
| TOML invalido | `Invalid TOML syntax: ...` |
| Falta `system.entrypoint` | `Field 'system.entrypoint' is required.` |
| `system.entrypoint != orchestrator` | `Entrypoint must be 'orchestrator' in v2.` |
| Falta lista `prompt_agents` | `At least one [[prompt_agents]] entry is required.` |
| Prompt vacio | `Prompt agent '{id}' must define non-empty 'prompt'.` |
| ID duplicado | `Duplicate prompt agent id '{id}'.` |
| Exceso de agentes | `Definition exceeds max agents: {n} > 10.` |
| Campo prohibido detectado | `Field '{field}' is not allowed in prompt-only schema.` |
| Version mismatch | `Version conflict: expected {n}, got {m}` |

## Behaviour

### Happy path

1. Usuario crea un nuevo agente escribiendo solo un prompt.
2. Front valida y guarda TOML v2.
3. Chat runtime recompila orquestador con ese agente custom.
4. Respuestas pueden delegar al nuevo agente segun su prompt.

### Fallback

1. Si no existe doc custom, builder usa template default prompt-only.
2. Si custom invalido, fallback a default y warning en logs.

## Migration from legacy schema

Compatibilidad de transicion recomendada:

1. Si se detecta schema legacy `[[agents]]` al leer:
   - extraer solo instrucciones inline de agentes custom.
   - ignorar `tools`, `skill`, `sub_agents`, `planner`, `wiki_context`.
   - mapear a `[[prompt_agents]]`.
2. En el primer `PUT` exitoso, persistir ya en formato v2.

Objetivo: evitar ruptura para atletas con definiciones anteriores, pero converger a prompt-only.

## Error Modes

| Escenario | HTTP | Detalle |
|-----------|------|---------|
| TOML invalido | 400 | Parse/validation error |
| Validacion semantica falla | 400 | Lista de errores |
| Version conflict | 409 | Optimistic locking |
| Unauthorized | 401 | Token interno invalido/faltante |
| Error interno | 500 | Falla no controlada |

## Dependencies

- `tomllib` / `tomli`
- Firestore (`firebase_admin`)
- Google ADK (`LlmAgent`, `AgentTool`, `PlanReActPlanner`)

## Open Questions

1. `order` sera editable (drag/drop) o append-only?
2. La UI mostrara un titulo derivado de prompt (primeras N palabras) o solo ID?
3. En migracion legacy, se aceptan skills para convertir a prompts via carga de `SKILL.md`, o se ignoran por completo?
