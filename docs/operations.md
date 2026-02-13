# Operations Guide

## Runbooks and Incident Handling

- Common incidents: `rag-engine/runbooks/common-incidents.md`
- Worker and ingestion behavior context: `rag-engine/flows-and-diagrams.md`

## Runtime Configuration

- Canonical config reference: `rag-engine/configuration.md`
- Prioritize secure handling for `SUPABASE_SERVICE_ROLE_KEY` and `RAG_SERVICE_SECRET`.

## Deployment Notes

- Historical deployment draft: `historical/deployment.md`
- Verify deployment docs against current stack before production rollout.

## Operational Checklist

- API healthy at `/health`.
- Orchestrator API healthy at `http://localhost:8001/health` when split mode is enabled.
- Worker process running and consuming `ingest_document` jobs from `job_queue`.
- Required environment variables loaded.
- Ingestion and retrieval smoke tests passing.

## Collection Behavior (Current)

- Collections are treated as overwrite-friendly in CLI workflows.
- `ing.sh` applies cleanup when reusing an existing collection before starting a new batch.
- Batch sealing endpoint remains available, but the default CLI path does not auto-seal collections.

## Observability and Reliability

- Keep error patterns and mitigations documented in runbooks.
- When introducing architecture changes, add/update ADR plus runbook notes.
