# ADR-0004: Table Pruning Before Generation

- Status: Proposed
- Date: 2026-02-12
- Source: `s.txt` section 5.2

## Context

Large tabular payloads increase token pressure and trigger lost-in-the-middle behavior in downstream generation.

## Decision

Introduce a deterministic table-pruning stage before final generation:

- Input: user query + structured table payload.
- Output: reduced table context focused on relevant rows/columns.
- Guardrail: preserve citation traceability to original source.

## Consequences

- Lower context-token usage and improved answer relevance.
- Additional implementation complexity and validation requirements.
