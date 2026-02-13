# ADR-0003: Hybrid Retrieval (Vector + Structured Signals)

- Status: Accepted
- Date: 2026-02-12
- Source: `s.txt` section 4.2

## Context

Pure semantic vector retrieval can miss exact numeric/tabular constraints even when topic similarity is high.

## Decision

Adopt hybrid retrieval:

- Vector similarity for semantic recall.
- Structured filters/signals (JSON/metadata/RPC constraints) for precision.
- Final authority-aware reranking before context delivery.

## Consequences

- Better precision for policy/numeric queries.
- More query complexity in database and RPC functions.
