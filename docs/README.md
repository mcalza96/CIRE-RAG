# Documentation

This is the canonical documentation entrypoint for CIRE-RAG.
Use this file as single source of truth for navigation.

## Start Here

- New contributor onboarding: `getting-started.md`
- Local development and quality gates: `developer-guide.md`
- System architecture and ADRs: `architecture.md`
- Operations, incidents, and deployment context: `operations.md`

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
- Incident response: `rag-engine/runbooks/common-incidents.md`
- Config and env vars: `rag-engine/configuration.md`

## Documentation Inventory

- Project overview: `../README.md`
- Contribution guide: `../CONTRIBUTING.md`
- Service README: `rag-engine/README.md`
- Service architecture deep dive: `rag-engine/architecture.md`
- Flow diagrams: `rag-engine/flows-and-diagrams.md`
- ADR index: `rag-engine/adr/README.md`
- Configuration reference: `rag-engine/configuration.md`
- Testing guide: `rag-engine/testing.md`
- Runbooks: `rag-engine/runbooks/common-incidents.md`
- Cost optimization runbook: `rag-engine/runbooks/cloud-cost-on-off.md`

## Historical and Planning Notes

No active historical/planning files are currently tracked.
