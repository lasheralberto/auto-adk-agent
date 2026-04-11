# Graph Report - .  (2026-04-10)

## Corpus Check
- 19 files · ~0 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 235 nodes · 463 edges · 20 communities detected
- Extraction: 48% EXTRACTED · 52% INFERRED · 0% AMBIGUOUS · INFERRED: 239 edges (avg confidence: 0.5)
- Token cost: 0 input · 0 output

## God Nodes (most connected - your core abstractions)
1. `AthleteStateStore` - 20 edges
2. `ArtifactStore` - 14 edges
3. `CyclingEnv` - 13 edges
4. `StravaConnector` - 11 edges
5. `_run_once()` - 10 edges
6. `run_ingestion()` - 10 edges
7. `research_wiki_pipeline()` - 10 edges
8. `_to_optional_int()` - 10 edges
9. `exchange_strava_auth_code()` - 9 edges
10. `run_training_pipeline()` - 7 edges

## Surprising Connections (you probably didn't know these)
- `StravaConnector` --uses--> `AthleteStateStore`  [INFERRED]
  agent\tools\pipeline\connectors\strava.py → agent\tools\pipeline\storage_backend.py
- `Conector para la API de Strava.      Encapsula autenticación OAuth y las llamada` --uses--> `AthleteStateStore`  [INFERRED]
  agent\tools\pipeline\connectors\strava.py → agent\tools\pipeline\storage_backend.py
- `Pipeline de ingesta agnóstico del servicio.      Delega la obtención de datos` --uses--> `ArtifactStore`  [INFERRED]
  agent\tools\pipeline\workflow.py → agent\tools\pipeline\storage_backend.py
- `Wrapper de compatibilidad que usa el conector de Strava.` --uses--> `ArtifactStore`  [INFERRED]
  agent\tools\pipeline\workflow.py → agent\tools\pipeline\storage_backend.py
- `Pipeline de ingesta agnóstico del servicio.      Delega la obtención de datos` --uses--> `AthleteStateStore`  [INFERRED]
  agent\tools\pipeline\workflow.py → agent\tools\pipeline\storage_backend.py

## Communities

### Community 0 - "Community 0"
Cohesion: 0.1
Nodes (41): _allowed_strava_redirect_uris(), build_orchestrator(), _build_orchestrator_instruction(), _build_plan_react_kwargs(), _build_strava_auth_url(), chat_agent(), chat_wiki_agent(), _create_plan_react_planner_agent() (+33 more)

### Community 1 - "Community 1"
Cohesion: 0.12
Nodes (26): _append_unique_tool_calls(), _build_prompt_with_precomputed_context(), _build_structured_payload(), _coerce_score(), _extract_confidence_score(), _extract_nested_tool_calls(), _extract_plan_react_sections(), _extract_response_text() (+18 more)

### Community 2 - "Community 2"
Cohesion: 0.13
Nodes (16): build_token_payload(), complete_oauth(), CyclingEnv, exchange_authorization_code(), get_strava_data(), get_strava_tokens(), make_predictions(), parse_authorization_code() (+8 more)

### Community 3 - "Community 3"
Cohesion: 0.15
Nodes (28): _activity_day(), _aggregate_research_metrics(), _average(), _build_activity_metadata(), _build_research_record_from_activity(), _build_wiki_hits(), _compact_timestamp(), _latest_research_input_path_for_day() (+20 more)

### Community 4 - "Community 4"
Cohesion: 0.13
Nodes (9): ABC, DataConnector, Interfaz abstracta para conectores de obtención de datos de actividad deportiva., DataConnector, Conector para la API de Strava.      Encapsula autenticación OAuth y las llamada, _strava_get(), StravaConnector, Wrapper de compatibilidad que usa el conector de Strava. (+1 more)

### Community 5 - "Community 5"
Cohesion: 0.28
Nodes (3): AthleteStateStore, _to_int(), utc_now_iso()

### Community 6 - "Community 6"
Cohesion: 0.56
Nodes (1): ArtifactStore

### Community 7 - "Community 7"
Cohesion: 0.38
Nodes (9): _build_dataset_digest(), _build_research_prompt(), _compact_record(), _extract_final_report(), _get_interaction(), _poll_until_complete(), _post_interaction(), run_deep_research_wiki_agent() (+1 more)

### Community 8 - "Community 8"
Cohesion: 0.5
Nodes (8): configure_iam(), create_or_update_secrets(), get_service_account(), load_env(), main(), secrets.py — gestión de secretos en GCP Secret Manager para strava-api  Lee los, run(), secret_exists()

### Community 9 - "Community 9"
Cohesion: 0.43
Nodes (6): _rag_stream_generator(), Helper to format SSE events., Bridges the async run_agent_streaming generator into a sync Flask generator via, Generador que incluye el evento de RAG y luego el streaming original del orquest, _sse(), _stream_generator()

### Community 10 - "Community 10"
Cohesion: 0.4
Nodes (5): build_wiki_research_chat_agent(), _get_wiki_bucket_name(), Read wiki/athlete_id/research.md from the GCS wiki bucket.      Returns the file, Build an LlmAgent that answers user questions based on wiki research content., read_wiki_research_md()

### Community 11 - "Community 11"
Cohesion: 0.4
Nodes (2): load_environment_from_sources(), Load environment variables from `.env`.

### Community 12 - "Community 12"
Cohesion: 0.5
Nodes (2): _configure_vertex_backend(), Configure ADK/GenAI clients to use Vertex AI instead of API key mode.

### Community 13 - "Community 13"
Cohesion: 1.0
Nodes (1): Obtiene datos del perfil del ciclista y sus actividades de Strava.         Calc

### Community 14 - "Community 14"
Cohesion: 1.0
Nodes (1): Entrena un modelo PPO sobre el entorno CyclingEnv y lo guarda en disco.

### Community 15 - "Community 15"
Cohesion: 1.0
Nodes (1): Carga el modelo guardado y realiza predicciones para los próximos N días.

### Community 16 - "Community 16"
Cohesion: 1.0
Nodes (1): Identificador único del conector (p. ej. 'strava', 'garmin').

### Community 17 - "Community 17"
Cohesion: 1.0
Nodes (1): Devuelve los atletas que pueden sincronizarse via este conector.          Cada e

### Community 18 - "Community 18"
Cohesion: 1.0
Nodes (1): Obtiene el perfil del atleta desde el servicio externo.          Devuelve el pay

### Community 19 - "Community 19"
Cohesion: 1.0
Nodes (1): Obtiene las actividades del atleta posteriores a ``after_epoch``.          Gesti

## Knowledge Gaps
- **23 isolated node(s):** `Read wiki/athlete_id/research.md from the GCS wiki bucket.      Returns the file`, `Build an LlmAgent that answers user questions based on wiki research content.`, `Configure ADK/GenAI clients to use Vertex AI instead of API key mode.`, `Load environment variables from `.env`.`, `StravaData` (+18 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 13`** (1 nodes): `Obtiene datos del perfil del ciclista y sus actividades de Strava.         Calc`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 14`** (1 nodes): `Entrena un modelo PPO sobre el entorno CyclingEnv y lo guarda en disco.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 15`** (1 nodes): `Carga el modelo guardado y realiza predicciones para los próximos N días.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 16`** (1 nodes): `Identificador único del conector (p. ej. 'strava', 'garmin').`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 17`** (1 nodes): `Devuelve los atletas que pueden sincronizarse via este conector.          Cada e`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 18`** (1 nodes): `Obtiene el perfil del atleta desde el servicio externo.          Devuelve el pay`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 19`** (1 nodes): `Obtiene las actividades del atleta posteriores a ``after_epoch``.          Gesti`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `AthleteStateStore` connect `Community 5` to `Community 4`?**
  _High betweenness centrality (0.073) - this node is a cross-community bridge._
- **Why does `ArtifactStore` connect `Community 6` to `Community 4`, `Community 5`?**
  _High betweenness centrality (0.058) - this node is a cross-community bridge._
- **Why does `StravaConnector` connect `Community 4` to `Community 5`?**
  _High betweenness centrality (0.045) - this node is a cross-community bridge._
- **Are the 4 inferred relationships involving `AthleteStateStore` (e.g. with `StravaConnector` and `Conector para la API de Strava.      Encapsula autenticación OAuth y las llamada`) actually correct?**
  _`AthleteStateStore` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `ArtifactStore` (e.g. with `Pipeline de ingesta agnóstico del servicio.      Delega la obtención de datos` and `Wrapper de compatibilidad que usa el conector de Strava.`) actually correct?**
  _`ArtifactStore` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `StravaConnector` (e.g. with `AthleteStateStore` and `DataConnector`) actually correct?**
  _`StravaConnector` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 9 inferred relationships involving `_run_once()` (e.g. with `_structured_output_enabled()` and `_append_unique_tool_calls()`) actually correct?**
  _`_run_once()` has 9 INFERRED edges - model-reasoned connections that need verification._