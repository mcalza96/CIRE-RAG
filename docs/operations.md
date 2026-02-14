# Operations Guide

## Runbooks and Incident Handling

- Common incidents: `rag-engine/runbooks/common-incidents.md`
- Worker and ingestion behavior context: `rag-engine/flows-and-diagrams.md`

## Runtime Configuration

- Canonical config reference: `rag-engine/configuration.md`
- Prioritize secure handling for `SUPABASE_SERVICE_ROLE_KEY` and `RAG_SERVICE_SECRET`.

## Deployment Notes

- Verify deployment docs against current stack before production rollout.

## Operational Checklist

- API healthy at `/health`.
- Worker process running and consuming `ingest_document` jobs from `job_queue`.
- Required environment variables loaded.
- Ingestion and retrieval smoke tests passing.

## API Lifecycle Checklist

- Contrato soportado: `/api/v1/chat/*`, `/api/v1/documents/*`, `/api/v1/management/*`, `/api/v1/retrieval/*`, `/api/v1/debug/retrieval/*`, `/api/v1/ingestion/*`.
- Endpoints retirados: `/api/v1/knowledge/retrieve`, `/api/v1/retrieval/chunks` (legacy), `/api/v1/retrieval/summaries` (legacy).
- Verificar que no existan referencias a rutas retiradas en clientes/scripts.

## Collection Behavior (Current)

- Collections are treated as overwrite-friendly in certain client workflows.
- Batch sealing endpoint remains available, but default ingestion paths may not auto-seal collections.

## Observability and Reliability

- Keep error patterns and mitigations documented in runbooks.
- When introducing architecture changes, add/update ADR plus runbook notes.
