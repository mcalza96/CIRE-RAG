-- Create job_status enum
CREATE TYPE public.job_status AS ENUM ('pending', 'processing', 'completed', 'failed');

-- Create job_queue table
CREATE TABLE public.job_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_type VARCHAR(255) NOT NULL,
    status public.job_status NOT NULL DEFAULT 'pending',
    payload JSONB NOT NULL,
    result JSONB,
    error_message TEXT,
    tenant_id UUID NOT NULL, -- For RLS and isolation
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Access Policy
ALTER TABLE public.job_queue ENABLE ROW LEVEL SECURITY;

-- Allow services to CRUD (Service Role will bypass RLS, but for good measure if we use authenticated client)
-- Ideally, the worker uses Service Role. The API uses Service Role or Authenticated User?
-- The API uses the user's token usually, but for writing to a queue that a worker picks up, 
-- usually the insertion is done with the user's permissions if RLS allows it, or the API acts as a privileged producer.
-- Let's assume the API might write as the user.
-- But wait, the worker needs to pick it up. The worker usually has admin privileges (Postgres role).

-- SIMPLE RLS for now:
-- Users can see their own jobs (if we track user_id, which we should inside payload or explicit column).
-- Let's add user_id column for RLS.
ALTER TABLE public.job_queue ADD COLUMN user_id UUID DEFAULT auth.uid();

CREATE POLICY "Users can create jobs" ON public.job_queue
    FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can view their own jobs" ON public.job_queue
    FOR SELECT USING (auth.uid() = user_id);

-- Indexes for polling
CREATE INDEX idx_job_queue_status_created_at ON public.job_queue (status, created_at);

-- RPC for Worker Polling (Atomic Fetch & Lock)
CREATE OR REPLACE FUNCTION public.fetch_next_job(p_job_type VARCHAR)
RETURNS TABLE (
    id UUID,
    job_type VARCHAR,
    payload JSONB,
    tenant_id UUID
) 
LANGUAGE plpgsql
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
