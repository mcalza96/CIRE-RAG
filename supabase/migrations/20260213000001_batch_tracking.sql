-- =============================================================================
-- MIGRATION: Batch tracking + atomic progress updates
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS public.ingestion_batches (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    collection_id uuid REFERENCES public.collections(id),
    total_files int NOT NULL DEFAULT 0,
    completed int NOT NULL DEFAULT 0,
    failed int NOT NULL DEFAULT 0,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'completed', 'partial', 'failed')),
    auto_seal boolean NOT NULL DEFAULT false,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ingestion_batches_tenant ON public.ingestion_batches(tenant_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_batches_collection ON public.ingestion_batches(collection_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_batches_status ON public.ingestion_batches(status);

ALTER TABLE public.source_documents
    ADD COLUMN IF NOT EXISTS batch_id uuid REFERENCES public.ingestion_batches(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_source_documents_batch ON public.source_documents(batch_id);

CREATE OR REPLACE FUNCTION public.update_batch_progress(
    p_batch_id uuid,
    p_success boolean
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_row public.ingestion_batches%ROWTYPE;
    v_status text;
BEGIN
    IF p_batch_id IS NULL THEN
        RETURN jsonb_build_object('ok', false, 'reason', 'missing_batch_id');
    END IF;

    SELECT *
    INTO v_row
    FROM public.ingestion_batches
    WHERE id = p_batch_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('ok', false, 'reason', 'batch_not_found', 'batch_id', p_batch_id);
    END IF;

    IF p_success THEN
        v_row.completed := v_row.completed + 1;
    ELSE
        v_row.failed := v_row.failed + 1;
    END IF;

    IF (v_row.completed + v_row.failed) >= v_row.total_files AND v_row.total_files > 0 THEN
        IF v_row.failed = 0 THEN
            v_status := 'completed';
        ELSIF v_row.completed = 0 THEN
            v_status := 'failed';
        ELSE
            v_status := 'partial';
        END IF;
    ELSE
        v_status := 'processing';
    END IF;

    UPDATE public.ingestion_batches
    SET
        completed = v_row.completed,
        failed = v_row.failed,
        status = v_status,
        updated_at = now()
    WHERE id = p_batch_id;

    IF v_row.auto_seal AND v_status = 'completed' AND v_row.collection_id IS NOT NULL THEN
        UPDATE public.collections
        SET status = 'sealed'
        WHERE id = v_row.collection_id;
    END IF;

    RETURN jsonb_build_object(
        'ok', true,
        'batch_id', p_batch_id,
        'completed', v_row.completed,
        'failed', v_row.failed,
        'total_files', v_row.total_files,
        'status', v_status
    );
END;
$$;

GRANT EXECUTE ON FUNCTION public.update_batch_progress(uuid, boolean) TO service_role;

COMMIT;
