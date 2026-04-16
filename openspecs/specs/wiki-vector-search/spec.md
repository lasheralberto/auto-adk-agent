# Spec: Wiki Vector Search

## Purpose
Pinecone-backed semantic indexing and search over wiki-research insights. Provides the retrieval layer for wiki-chat and any future RAG flow.

## Scope
- In scope:
  - Embedding generation
  - Pinecone upsert / query
  - Per-athlete namespace management
- Out of scope:
  - Insight generation (see `wiki-research`)
  - Chat orchestration (see `wiki-chat`)

## Source Anchors
- `agent/tools/pipeline/wiki_vector_index.py`
- `agent/tools/pipeline/wiki_llm.py`

## Public API / Endpoints
<invoked via pipeline indexing stage + wiki-chat retrieval>

## Inputs & Outputs
<input: wiki pages; output: vector ids, query results with scores>

## Dependencies
- Pinecone (index + namespace)
- Embedding model (litellm / OpenAI)
- Storage backend for source pages

## Behaviour
<happy path, re-index on page update, deletion, query top-k + filter>

## Open Questions
<TBD>
