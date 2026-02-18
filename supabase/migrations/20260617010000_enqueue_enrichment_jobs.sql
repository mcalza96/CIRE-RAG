-- Queue performance + idempotency guard for deferred enrichment jobs.

CREATE INDEX IF NOT EXISTS idx_job_queue_enrich_pending
ON public.job_queue (job_type, status, created_at)
WHERE job_type = 'enrich_document' AND status IN ('pending', 'processing');

CREATE UNIQUE INDEX IF NOT EXISTS idx_job_queue_enrich_doc_unique_active
ON public.job_queue ((payload->>'source_document_id'))
WHERE job_type = 'enrich_document' AND status IN ('pending', 'processing');
