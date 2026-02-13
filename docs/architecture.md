# Architecture Map

Use this page as a map, then jump into the detailed service docs.

## Core Architecture

- System architecture deep dive: `rag-engine/architecture.md`
- One-page architecture snapshot: `rag-engine/one-page-architecture.md`
- Flow diagrams and execution paths: `rag-engine/flows-and-diagrams.md`

## Key Design Decisions

- ADR index: `rag-engine/adr/README.md`
- Active ADRs are versioned in `rag-engine/adr/`.

## Main Runtime Components

- API entrypoint: `app/main.py`
- API routes: `app/api/v1/api_router.py`
- Worker runtime: `app/worker.py`
- Ingestion and retrieval engines: `app/services/`

## Related References

- Configuration: `rag-engine/configuration.md`
- Testing strategy: `rag-engine/testing.md`
- Runbooks: `rag-engine/runbooks/common-incidents.md`
