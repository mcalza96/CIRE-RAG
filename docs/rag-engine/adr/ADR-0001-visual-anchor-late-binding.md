# ADR-0001: Visual Anchor with Late Binding

- Status: Accepted
- Date: 2026-02-12
- Source: `s.txt` sections 2.1, 2.2, 8

## Context

CIRE-RAG handles dense enterprise/regulatory PDFs with mixed modalities (text + tables + figures). Native multimodal indexing is costly and less explainable for audit-grade workflows.

## Decision

Use a late-binding "Visual Anchor" architecture:

- Parse visual content at ingestion time into structured payloads.
- Index and retrieve primarily through textual/structured representations.
- Hydrate visual context only when selected by retrieval.

## Consequences

- Better cost and latency profile for small teams.
- Higher explainability of retrieved evidence.
- Requires robust parser and validation controls in ingestion.
