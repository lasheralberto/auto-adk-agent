# Graph Report - .  (2026-04-13)

## Corpus Check
- 33 files · ~0 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 379 nodes · 748 edges · 24 communities detected
- Extraction: 50% EXTRACTED · 50% INFERRED · 0% AMBIGUOUS · INFERRED: 376 edges (avg confidence: 0.5)
- Token cost: 0 input · 0 output

## God Nodes (most connected - your core abstractions)
1. `AthleteStateStore` - 38 edges
2. `ArtifactStore` - 28 edges
3. `StravaAgentClient` - 23 edges
4. `StravaConnector` - 17 edges
5. `AuthService` - 16 edges
6. `research_wiki_pipeline()` - 14 edges
7. `CyclingEnv` - 13 edges
8. `DataConnector` - 13 edges
9. `ValidationError` - 11 edges
10. `_run_once()` - 10 edges

## Surprising Connections (you probably didn't know these)
- `Devuelve el contexto de la wiki relevante para la pregunta.      - Si hay ``quer` --uses--> `ArtifactStore`  [INFERRED]
  agent\agents\wiki_research_chat_agent.py → agent\tools\pipeline\storage_backend.py
- `Build an LlmAgent that answers user questions based on wiki content.` --uses--> `ArtifactStore`  [INFERRED]
  agent\agents\wiki_research_chat_agent.py → agent\tools\pipeline\storage_backend.py
- `StravaConnector` --uses--> `AthleteStateStore`  [INFERRED]
  agent\tools\pipeline\connectors\strava.py → agent\tools\pipeline\storage_backend.py
- `Conector para la API de Strava.      Encapsula autenticación OAuth y las llamada` --uses--> `AthleteStateStore`  [INFERRED]
  agent\tools\pipeline\connectors\strava.py → agent\tools\pipeline\storage_backend.py
- `Obtiene las ``limit`` actividades más recientes del atleta.          A diferenci` --uses--> `AthleteStateStore`  [INFERRED]
  agent\tools\pipeline\connectors\strava.py → agent\tools\pipeline\storage_backend.py

## Communities

### Community 0 - "Community 0"
Cohesion: 0.08
Nodes (17): Re-indexa en Pinecone las páginas existentes de la wiki.      Acepta ``athlete, Lista las runs de indexación de actividades Strava para un atleta.      Consul, Lista sólo las actividades ya indexadas (status ``success`` o     ``partial_suc, ExternalServiceError, NotFoundError, Raised when a required resource does not exist., Raised when an upstream service call fails., Base exception for SDK errors. (+9 more)

### Community 1 - "Community 1"
Cohesion: 0.11
Nodes (32): format_page_catalog_for_prompt(), Catálogo de páginas de la wiki de atleta.  Define la estructura de páginas espec, Devuelve el catálogo de páginas formateado para incluir en prompts LLM., _activity_day(), _aggregate_activity_metrics(), _append_log_entry(), _build_activity_firestore_payload(), _build_activity_summary_for_prompt() (+24 more)

### Community 2 - "Community 2"
Cohesion: 0.13
Nodes (28): build_orchestrator(), _build_orchestrator_instruction(), _build_plan_react_kwargs(), chat_agent(), chat_wiki_agent(), _create_plan_react_planner_agent(), _dispatch_index_wiki_async(), _dispatch_research_wiki_async() (+20 more)

### Community 3 - "Community 3"
Cohesion: 0.15
Nodes (23): ArtifactStore, backfill_athlete(), delete_athlete_index(), _embed(), _embed_detailed(), _embedding_dimension(), _embedding_model(), _get_client() (+15 more)

### Community 4 - "Community 4"
Cohesion: 0.08
Nodes (9): Async-first SDK client for Strava Agent workflows., StravaAgentClient, _configure_vertex_backend(), from_env(), Configure ADK/GenAI clients to use Vertex AI instead of API key mode., SDKConfig, _split_csv(), _to_optional_int() (+1 more)

### Community 5 - "Community 5"
Cohesion: 0.12
Nodes (26): _append_unique_tool_calls(), _build_prompt_with_precomputed_context(), _build_structured_payload(), _coerce_score(), _extract_confidence_score(), _extract_nested_tool_calls(), _extract_plan_react_sections(), _extract_response_text() (+18 more)

### Community 6 - "Community 6"
Cohesion: 0.13
Nodes (16): build_token_payload(), complete_oauth(), CyclingEnv, exchange_authorization_code(), get_strava_data(), get_strava_tokens(), make_predictions(), parse_authorization_code() (+8 more)

### Community 7 - "Community 7"
Cohesion: 0.11
Nodes (15): ABC, DataConnector, Interfaz abstracta para conectores de obtención de datos de actividad deportiva., DataConnector, Conector para la API de Strava.      Encapsula autenticación OAuth y las llamada, Obtiene las ``limit`` actividades más recientes del atleta.          A diferenci, _strava_get(), StravaConnector (+7 more)

### Community 8 - "Community 8"
Cohesion: 0.25
Nodes (4): AthleteStateStore, Lista runs de actividades (cualquier status) ordenadas desc por fecha., _to_int(), utc_now_iso()

### Community 9 - "Community 9"
Cohesion: 0.21
Nodes (7): AuthService, _http_error_payload(), _normalize_redirect_uri(), _normalize_requested_scope(), _normalize_token_payload(), _state_b64decode(), _state_b64encode()

### Community 10 - "Community 10"
Cohesion: 0.16
Nodes (16): bootstrap_wiki(), _call_llm(), format_log_entry(), generate_index(), _get_model(), _parse_json_response(), Motor LLM para la wiki de atleta.  Reemplaza la integración con Google Deep Rese, Determina qué páginas de la wiki afecta una nueva actividad. (+8 more)

### Community 11 - "Community 11"
Cohesion: 0.38
Nodes (9): _build_dataset_digest(), _build_research_prompt(), _compact_record(), _extract_final_report(), _get_interaction(), _poll_until_complete(), _post_interaction(), run_deep_research_wiki_agent() (+1 more)

### Community 12 - "Community 12"
Cohesion: 0.5
Nodes (8): configure_iam(), create_or_update_secrets(), get_service_account(), load_env(), main(), secrets.py — gestión de secretos en GCP Secret Manager para strava-api  Lee los, run(), secret_exists()

### Community 13 - "Community 13"
Cohesion: 0.42
Nodes (4): ChatService, _normalize_chat_result(), _normalize_planner_mode(), _normalize_response_format()

### Community 14 - "Community 14"
Cohesion: 0.36
Nodes (7): build_wiki_research_chat_agent(), _list_wiki_slugs(), Devuelve el contexto de la wiki relevante para la pregunta.      - Si hay ``quer, Build an LlmAgent that answers user questions based on wiki content., _read_index(), _read_page(), read_wiki_content()

### Community 15 - "Community 15"
Cohesion: 0.43
Nodes (6): _rag_stream_generator(), Helper to format SSE events., Bridges the async run_agent_streaming generator into a sync Flask generator via, Generador que incluye el evento de RAG y luego el streaming original del orquest, _sse(), _stream_generator()

### Community 16 - "Community 16"
Cohesion: 0.4
Nodes (2): load_environment_from_sources(), Load environment variables from `.env`.

### Community 17 - "Community 17"
Cohesion: 1.0
Nodes (1): Obtiene datos del perfil del ciclista y sus actividades de Strava.         Calc

### Community 18 - "Community 18"
Cohesion: 1.0
Nodes (1): Entrena un modelo PPO sobre el entorno CyclingEnv y lo guarda en disco.

### Community 19 - "Community 19"
Cohesion: 1.0
Nodes (1): Carga el modelo guardado y realiza predicciones para los próximos N días.

### Community 20 - "Community 20"
Cohesion: 1.0
Nodes (1): Identificador único del conector (p. ej. 'strava', 'garmin').

### Community 21 - "Community 21"
Cohesion: 1.0
Nodes (1): Devuelve los atletas que pueden sincronizarse via este conector.          Cada e

### Community 22 - "Community 22"
Cohesion: 1.0
Nodes (1): Obtiene el perfil del atleta desde el servicio externo.          Devuelve el pay

### Community 23 - "Community 23"
Cohesion: 1.0
Nodes (1): Obtiene las actividades del atleta posteriores a ``after_epoch``.          Gesti

## Knowledge Gaps
- **36 isolated node(s):** `Configure ADK/GenAI clients to use Vertex AI instead of API key mode.`, `Load environment variables from `.env`.`, `StravaData`, `Entorno Gymnasium para optimizar la planificación del entrenamiento ciclista.`, `Parámetros         ----------         strava_data  : dict devuelto por get_str` (+31 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 17`** (1 nodes): `Obtiene datos del perfil del ciclista y sus actividades de Strava.         Calc`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 18`** (1 nodes): `Entrena un modelo PPO sobre el entorno CyclingEnv y lo guarda en disco.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 19`** (1 nodes): `Carga el modelo guardado y realiza predicciones para los próximos N días.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 20`** (1 nodes): `Identificador único del conector (p. ej. 'strava', 'garmin').`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 21`** (1 nodes): `Devuelve los atletas que pueden sincronizarse via este conector.          Cada e`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 22`** (1 nodes): `Obtiene el perfil del atleta desde el servicio externo.          Devuelve el pay`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 23`** (1 nodes): `Obtiene las actividades del atleta posteriores a ``after_epoch``.          Gesti`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `AthleteStateStore` connect `Community 8` to `Community 0`, `Community 9`, `Community 13`, `Community 7`?**
  _High betweenness centrality (0.169) - this node is a cross-community bridge._
- **Why does `ArtifactStore` connect `Community 3` to `Community 8`, `Community 14`, `Community 7`?**
  _High betweenness centrality (0.100) - this node is a cross-community bridge._
- **Why does `StravaAgentClient` connect `Community 4` to `Community 0`?**
  _High betweenness centrality (0.081) - this node is a cross-community bridge._
- **Are the 17 inferred relationships involving `AthleteStateStore` (e.g. with `StravaConnector` and `Conector para la API de Strava.      Encapsula autenticación OAuth y las llamada`) actually correct?**
  _`AthleteStateStore` has 17 INFERRED edges - model-reasoned connections that need verification._
- **Are the 18 inferred relationships involving `ArtifactStore` (e.g. with `Devuelve el contexto de la wiki relevante para la pregunta.      - Si hay ``quer` and `Build an LlmAgent that answers user questions based on wiki content.`) actually correct?**
  _`ArtifactStore` has 18 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `StravaAgentClient` (e.g. with `SDKConfig` and `ChatResponse`) actually correct?**
  _`StravaAgentClient` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 9 inferred relationships involving `StravaConnector` (e.g. with `AthleteStateStore` and `DataConnector`) actually correct?**
  _`StravaConnector` has 9 INFERRED edges - model-reasoned connections that need verification._