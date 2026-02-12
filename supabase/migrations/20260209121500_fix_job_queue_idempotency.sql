-- Migration: Fix job_queue idempotency, add user_id, and ensure function exists
-- Date: 2026-02-09

BEGIN;

-- 1. Ensure functions for idempotent column addition
DO $$
BEGIN
    -- Idempotent Enum Creation (Workaround as CREATE TYPE IF NOT EXISTS is not standard in older PG, but we use Exception block)
    BEGIN
        CREATE TYPE public.job_status AS ENUM ('pending', 'processing', 'completed', 'failed');
    EXCEPTION
        WHEN duplicate_object THEN null;
    END;
END $$;

-- 2. Ensure Table Exists
CREATE TABLE IF NOT EXISTS public.job_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_type VARCHAR(255) NOT NULL,
    status public.job_status NOT NULL DEFAULT 'pending',
    payload JSONB NOT NULL,
    result JSONB,
    error_message TEXT,
    tenant_id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 3. Add Columns Idempotently (if missing)
DO $$
BEGIN
    ALTER TABLE public.job_queue ADD COLUMN IF NOT EXISTS user_id UUID DEFAULT auth.uid();
EXCEPTION
    WHEN duplicate_column THEN null;
END $$;

-- 4. Policies (Drop first to avoid duplication error)
DROP POLICY IF EXISTS "Users can create jobs" ON public.job_queue;
DROP POLICY IF EXISTS "Users can view their own jobs" ON public.job_queue;
-- Service role always has access
ALTER TABLE public.job_queue ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can create jobs" ON public.job_queue
    FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can view their own jobs" ON public.job_queue
    FOR SELECT USING (auth.uid() = user_id);

-- 5. Indexes
CREATE INDEX IF NOT EXISTS idx_job_queue_status_created_at ON public.job_queue (status, created_at);

-- 6. RPC Function (Atomic Fetch)
CREATE OR REPLACE FUNCTION public.fetch_next_job(p_job_type VARCHAR)
RETURNS TABLE (
    id UUID,
    job_type VARCHAR,
    payload JSONB,
    tenant_id UUID
) 
LANGUAGE plpgsql
SECURITY DEFINER -- Run as owner (likely postgres/superuser) to bypass RLS for the worker polling if needed, or stick to invoker if worker uses service role.
-- Note: Worker uses Service Role, so RLS is bypassed anyway. SECURITY DEFINER is optional but safe here.
AS $$
BEGIN
    RETURN QUERY
    UPDATE public.job_queue
    SET status = 'processing',
        updated_at = NOW()
    WHERE job_queue.id = (
        SELECT job_queue.id
        FROM public.job_queue
        WHERE status = 'pending'
        AND (p_job_type IS NULL OR job_queue.job_type = p_job_type)
        ORDER BY created_at ASC
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    )
    RETURNING job_queue.id, job_queue.job_type, job_queue.payload, job_queue.tenant_id;
END;
$$;

COMMIT;
