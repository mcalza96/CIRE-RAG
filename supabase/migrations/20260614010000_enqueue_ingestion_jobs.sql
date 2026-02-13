-- Ensure ingest_document jobs are enqueued from source_documents transitions.

CREATE INDEX IF NOT EXISTS idx_job_queue_ingest_source_pending
ON public.job_queue ((payload->>'source_document_id'), status)
WHERE job_type = 'ingest_document' AND status IN ('pending', 'processing');

CREATE OR REPLACE FUNCTION public.enqueue_ingest_job_from_source_documents()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_source_document_id TEXT;
    v_tenant_id UUID;
BEGIN
    IF NEW.status <> 'queued' THEN
        RETURN NEW;
    END IF;

    v_source_document_id := NEW.id::TEXT;
    v_tenant_id := COALESCE(NEW.institution_id, '00000000-0000-0000-0000-000000000000'::UUID);

    IF EXISTS (
        SELECT 1
        FROM public.job_queue jq
        WHERE jq.job_type = 'ingest_document'
          AND jq.status IN ('pending', 'processing')
          AND jq.payload->>'source_document_id' = v_source_document_id
    ) THEN
        RETURN NEW;
    END IF;

    INSERT INTO public.job_queue (job_type, status, payload, tenant_id)
    VALUES (
        'ingest_document',
        'pending',
        jsonb_build_object(
            'source_document_id', v_source_document_id,
            'triggered_by', 'source_documents',
            'status', NEW.status
        ),
        v_tenant_id
    );

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_enqueue_ingest_job_on_source_documents ON public.source_documents;

CREATE TRIGGER trg_enqueue_ingest_job_on_source_documents
AFTER INSERT OR UPDATE OF status ON public.source_documents
FOR EACH ROW
WHEN (NEW.status = 'queued')
EXECUTE FUNCTION public.enqueue_ingest_job_from_source_documents();

-- Backfill queued documents so they are visible to pull workers immediately.
INSERT INTO public.job_queue (job_type, status, payload, tenant_id)
SELECT
    'ingest_document',
    'pending',
    jsonb_build_object(
        'source_document_id', sd.id::TEXT,
        'triggered_by', 'migration_backfill',
        'status', sd.status
    ),
    COALESCE(sd.institution_id, '00000000-0000-0000-0000-000000000000'::UUID)
FROM public.source_documents sd
WHERE sd.status = 'queued'
  AND NOT EXISTS (
      SELECT 1
      FROM public.job_queue jq
      WHERE jq.job_type = 'ingest_document'
        AND jq.status IN ('pending', 'processing')
        AND jq.payload->>'source_document_id' = sd.id::TEXT
  );
