# Retrieval Contract (RAG)

This document defines the *internal* retrieval payload contract used by the RAG service.

## Scope

- Applies to retrieval engines in `rag/app/services/retrieval/` and the retrieval tools exposed by the container.
- Applies to `KnowledgeService.get_grounded_context()` and the `/api/v1/chat/completions` evidence endpoint.

## Canonical Retrieval Row (dict)

Retrieval engines return `list[dict[str, Any]]` where each item follows this minimum shape:

```json
{
  "id": "<chunk-id or synthetic-id>",
  "content": "<text chunk or synthesized evidence>",
  "metadata": {"source_id": "<source_document_id>", "tenant_id": "<tenant>", "...": "..."},
  "similarity": 0.0,
  "score": 0.0,
  "source_layer": "vector|fts|hybrid|graph",
  "source_type": "content_chunk|knowledge_entity",
  "source_id": "<source id>"
}
```

Required keys:

- `id`: stable identifier for dedupe + citation
- `content`: text returned to the orchestrator
- `metadata`: dict (may be empty)
- `similarity`: float (can be derived)
- `score`: float (ranking score, layer-specific)
- `source_layer`: one of `vector`, `fts`, `hybrid`, `graph`

Notes:

- `id` is used for citations (`citations = [id, ...]`).
- `metadata.source_id` is used by tenant stamping / isolation checks.

## Grounded Context Response (API)

`KnowledgeService.get_grounded_context()` returns a dict with:

- `context_chunks`: list[str]
- `context_map`: dict[str, Any] keyed by retrieval-row `id`
- `citations`: list[str] (usually the same ids)
- `mode`: string label (e.g. `VECTOR_ONLY`, `HYBRID`, `AMBIGUOUS_SCOPE`)
- optional scope fields: `requires_scope_clarification`, `scope_message`, `requested_scopes`, `scope_mismatch_detected`

## Benchmark Script

Use `rag/scripts/bench_retrieval.py` to capture latency/payload baselines before changing retrieval semantics.
