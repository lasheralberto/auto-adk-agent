# Graph Report - .  (2026-04-09)

## Corpus Check
- 13 files · ~0 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 186 nodes · 383 edges · 13 communities detected
- Extraction: 46% EXTRACTED · 54% INFERRED · 0% AMBIGUOUS · INFERRED: 206 edges (avg confidence: 0.5)
- Token cost: 0 input · 0 output

## God Nodes (most connected - your core abstractions)
1. `AthleteStateStore` - 14 edges
2. `CyclingEnv` - 13 edges
3. `rag_wiki_pipeline()` - 13 edges
4. `run_pinecone_indexing()` - 12 edges
5. `ArtifactStore` - 11 edges
6. `_run_once()` - 10 edges
7. `run_strava_ingestion()` - 9 edges
8. `exchange_strava_auth_code()` - 9 edges
9. `_safe_int()` - 8 edges
10. `_to_optional_int()` - 8 edges

## Surprising Connections (you probably didn't know these)
- None detected - all connections are within the same source files.

## Communities

### Community 0 - "Community 0"
Cohesion: 0.12
Nodes (35): _allowed_strava_redirect_uris(), build_orchestrator(), _build_orchestrator_instruction(), _build_plan_react_kwargs(), _build_strava_auth_url(), chat_agent(), _create_plan_react_planner_agent(), _create_strava_oauth_state() (+27 more)

### Community 1 - "Community 1"
Cohesion: 0.12
Nodes (26): _append_unique_tool_calls(), _build_prompt_with_precomputed_context(), _build_structured_payload(), _coerce_score(), _extract_confidence_score(), _extract_nested_tool_calls(), _extract_plan_react_sections(), _extract_response_text() (+18 more)

### Community 2 - "Community 2"
Cohesion: 0.13
Nodes (16): build_token_payload(), complete_oauth(), CyclingEnv, exchange_authorization_code(), get_strava_data(), get_strava_tokens(), make_predictions(), parse_authorization_code() (+8 more)

### Community 3 - "Community 3"
Cohesion: 0.18
Nodes (28): _activity_day(), _build_pinecone_metadata(), _compact_timestamp(), _extract_activity_id_from_path(), _generate_activity_summary(), _get_genai_client(), _get_pinecone_index(), _get_pinecone_namespace() (+20 more)

### Community 4 - "Community 4"
Cohesion: 0.29
Nodes (3): AthleteStateStore, _to_int(), utc_now_iso()

### Community 5 - "Community 5"
Cohesion: 0.55
Nodes (1): ArtifactStore

### Community 6 - "Community 6"
Cohesion: 0.5
Nodes (8): configure_iam(), create_or_update_secrets(), get_service_account(), load_env(), main(), secrets.py — gestión de secretos en GCP Secret Manager para strava-api  Lee los, run(), secret_exists()

### Community 7 - "Community 7"
Cohesion: 0.43
Nodes (6): _rag_stream_generator(), Helper to format SSE events., Bridges the async run_agent_streaming generator into a sync Flask generator via, Generador que incluye el evento de RAG y luego el streaming original del orquest, _sse(), _stream_generator()

### Community 8 - "Community 8"
Cohesion: 0.4
Nodes (2): load_environment_from_sources(), Load environment variables from `.env`.

### Community 9 - "Community 9"
Cohesion: 0.5
Nodes (2): _configure_vertex_backend(), Configure ADK/GenAI clients to use Vertex AI instead of API key mode.

### Community 10 - "Community 10"
Cohesion: 1.0
Nodes (1): Obtiene datos del perfil del ciclista y sus actividades de Strava.         Calc

### Community 11 - "Community 11"
Cohesion: 1.0
Nodes (1): Entrena un modelo PPO sobre el entorno CyclingEnv y lo guarda en disco.

### Community 12 - "Community 12"
Cohesion: 1.0
Nodes (1): Carga el modelo guardado y realiza predicciones para los próximos N días.

## Knowledge Gaps
- **16 isolated node(s):** `Configure ADK/GenAI clients to use Vertex AI instead of API key mode.`, `Load environment variables from `.env`.`, `StravaData`, `Entorno Gymnasium para optimizar la planificación del entrenamiento ciclista.`, `Parámetros         ----------         strava_data  : dict devuelto por get_str` (+11 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 10`** (1 nodes): `Obtiene datos del perfil del ciclista y sus actividades de Strava.         Calc`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 11`** (1 nodes): `Entrena un modelo PPO sobre el entorno CyclingEnv y lo guarda en disco.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 12`** (1 nodes): `Carga el modelo guardado y realiza predicciones para los próximos N días.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ArtifactStore` connect `Community 5` to `Community 4`?**
  _High betweenness centrality (0.063) - this node is a cross-community bridge._
- **Are the 12 inferred relationships involving `rag_wiki_pipeline()` (e.g. with `_normalize_date()` and `_resolve_targets()`) actually correct?**
  _`rag_wiki_pipeline()` has 12 INFERRED edges - model-reasoned connections that need verification._
- **Are the 11 inferred relationships involving `run_pinecone_indexing()` (e.g. with `_normalize_date()` and `_resolve_targets()`) actually correct?**
  _`run_pinecone_indexing()` has 11 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Configure ADK/GenAI clients to use Vertex AI instead of API key mode.`, `Load environment variables from `.env`.`, `StravaData` to the rest of the system?**
  _16 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Community 0` be split into smaller, more focused modules?**
  _Cohesion score 0.12 - nodes in this community are weakly interconnected._
- **Should `Community 1` be split into smaller, more focused modules?**
  _Cohesion score 0.12 - nodes in this community are weakly interconnected._
- **Should `Community 2` be split into smaller, more focused modules?**
  _Cohesion score 0.13 - nodes in this community are weakly interconnected._