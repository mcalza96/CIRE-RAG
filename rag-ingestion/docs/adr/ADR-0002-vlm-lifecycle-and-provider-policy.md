# ADR-0002: VLM Lifecycle and Provider Policy

- Status: Accepted
- Date: 2026-02-12
- Source: `s.txt` section 3

## Context

Model lifecycle churn can create immediate technical debt if defaults are tied to short-lived versions.

## Decision

- Keep ingestion VLM provider/model configurable.
- Default to supported models with active lifecycle.
- Enforce provider/model compatibility validation at config load.
- Document migration policy when model families are deprecated.

## Consequences

- Lower operational risk from provider deprecations.
- Slightly higher configuration complexity.
