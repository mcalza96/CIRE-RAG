# Documentation

This is the canonical documentation entrypoint for CISRE (Cognitive Ingestion & Structured Retrieval Engine).

## Start Here

- New contributor onboarding: `getting-started.md`
- Local development and quality gates: `developer-guide.md`
- System architecture and ADRs: `architecture.md`
- Operations, incidents, and deployment context: `operations.md`

## Audience-Based Navigation

- Maintainers: architecture, ADRs, and runbooks first.
- Contributors: setup, coding workflow, and test policy.
- Integrators: API overview in `../rag-ingestion/README.md` and service docs.

## Source-of-Truth Policy

- Top-level docs (`docs/*`) define navigation and project-wide standards.
- Service-specific deep dives live in `rag-ingestion/docs/*`.
- Root `README.md` stays concise and links here.
- Every feature PR should update docs when behavior changes.

## Source of Truth by Topic

- Onboarding and local setup: `getting-started.md`
- Runtime architecture and flow semantics: `../rag-ingestion/docs/architecture.md`
- Sequence/operational diagrams: `../rag-ingestion/docs/flows-and-diagrams.md`
- HITL behavior and API examples: `../rag-ingestion/docs/getting-started.md`
- Incident response: `../rag-ingestion/docs/runbooks/common-incidents.md`
- Config and env vars: `../rag-ingestion/docs/configuration.md`

## Documentation Inventory

- Project overview: `../README.md`
- Contribution guide: `../CONTRIBUTING.md`
- Service README: `../rag-ingestion/README.md`
- Service HITL getting started: `../rag-ingestion/docs/getting-started.md`
- Service architecture deep dive: `../rag-ingestion/docs/architecture.md`
- Flow diagrams: `../rag-ingestion/docs/flows-and-diagrams.md`
- ADR index: `../rag-ingestion/docs/adr/README.md`
- Configuration reference: `../rag-ingestion/docs/configuration.md`
- Testing guide: `../rag-ingestion/docs/testing.md`
- Runbooks: `../rag-ingestion/docs/runbooks/common-incidents.md`

## Historical and Planning Notes

These files are useful context but are not canonical user docs:

- `../deployment.md`
- `../informe.md`
- `../plan_batch_ingestion_phase0.md`
