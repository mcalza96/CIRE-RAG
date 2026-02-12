-- Create ingestion_events table for Realtime RAG Console
CREATE TABLE IF NOT EXISTS public.ingestion_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_document_id UUID NOT NULL REFERENCES public.source_documents(id) ON DELETE CASCADE,
    message TEXT NOT NULL,
    node_type TEXT, -- e.g., 'UNIT', 'CHAPTER', 'TOPIC', 'SYSTEM'
    status TEXT NOT NULL DEFAULT 'INFO', -- 'INFO', 'SUCCESS', 'ERROR', 'WARN'
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Enable RLS
ALTER TABLE public.ingestion_events ENABLE ROW LEVEL SECURITY;

-- Policy: Admin View Only (Reading)
-- Assumption: Admins have a specific role or we just allow authenticated read for now for internal tools.
-- Ideally we check auth.uid() in profiles... for MVP allow authenticated read.
-- Drop if exists to avoid "already exists" error
DROP POLICY IF EXISTS "Allow authenticated read access" ON public.ingestion_events;
CREATE POLICY "Allow authenticated read access" ON public.ingestion_events
    FOR SELECT
    TO authenticated
    USING (true);

-- Policy: Service Role Write (Ingestion Script)
-- Implicitly allowed for service_role, no policy needed for it.

-- Enable Realtime (Guard against already added)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_publication_tables 
        WHERE pubname = 'supabase_realtime' 
        AND schemaname = 'public' 
        AND tablename = 'ingestion_events'
    ) THEN
        ALTER PUBLICATION supabase_realtime ADD TABLE public.ingestion_events;
    END IF;
END $$;

-- Create index for faster filtering by job
CREATE INDEX IF NOT EXISTS idx_ingestion_events_source_id ON public.ingestion_events(source_document_id);
