# Architecture Map

Use this page as a map, then jump into the detailed service docs.

## Core Architecture

- System architecture deep dive: `../rag-ingestion/docs/architecture.md`
- One-page architecture snapshot: `../rag-ingestion/docs/one-page-architecture.md`
- Flow diagrams and execution paths: `../rag-ingestion/docs/flows-and-diagrams.md`

## Key Design Decisions

- ADR index: `../rag-ingestion/docs/adr/README.md`
- Active ADRs are versioned in `../rag-ingestion/docs/adr/`.

## Main Runtime Components

- API entrypoint: `../rag-ingestion/app/main.py`
- API routes: `../rag-ingestion/app/api/v1/api_router.py`
- Worker runtime: `../rag-ingestion/app/worker.py`
- Ingestion and retrieval engines: `../rag-ingestion/app/services/`

## Related References

- Configuration: `../rag-ingestion/docs/configuration.md`
- Testing strategy: `../rag-ingestion/docs/testing.md`
- Runbooks: `../rag-ingestion/docs/runbooks/common-incidents.md`
