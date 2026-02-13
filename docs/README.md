# Documentation

This is the canonical documentation entrypoint for CISRE (Cognitive Ingestion & Structured Retrieval Engine).

## Start Here

- New contributor onboarding: `getting-started.md`
- Local development and quality gates: `developer-guide.md`
- System architecture and ADRs: `architecture.md`
- Operations, incidents, and deployment context: `operations.md`
- Local split migration plan (rag-engine + qa-orchestrator): `migration-rag-orchestrator-split-local.md`

## Audience-Based Navigation

- Maintainers: architecture, ADRs, and runbooks first.
- Contributors: setup, coding workflow, and test policy.
- Integrators: API overview in `rag-engine/README.md` and service docs.

## Source-of-Truth Policy

- Top-level docs (`docs/*`) define navigation and project-wide standards.
- Service-specific deep dives live in `docs/rag-engine/*`.
- Root `README.md` stays concise and links here.
- Every feature PR should update docs when behavior changes.

## Source of Truth by Topic

- Onboarding and local setup: `getting-started.md`
- Runtime architecture and flow semantics: `rag-engine/architecture.md`
- Sequence/operational diagrams: `rag-engine/flows-and-diagrams.md`
- HITL behavior and API examples: `rag-engine/getting-started.md`
- Incident response: `rag-engine/runbooks/common-incidents.md`
- Config and env vars: `rag-engine/configuration.md`

## Documentation Inventory

- Project overview: `../README.md`
- Contribution guide: `../CONTRIBUTING.md`
- Service README: `rag-engine/README.md`
- Service HITL getting started: `rag-engine/getting-started.md`
- Service architecture deep dive: `rag-engine/architecture.md`
- Flow diagrams: `rag-engine/flows-and-diagrams.md`
- ADR index: `rag-engine/adr/README.md`
- Configuration reference: `rag-engine/configuration.md`
- Testing guide: `rag-engine/testing.md`
- Runbooks: `rag-engine/runbooks/common-incidents.md`

## Historical and Planning Notes

These files are useful context but are not canonical user docs:

- `historical/deployment.md`
- `../plan_batch_ingestion_phase0.md`
