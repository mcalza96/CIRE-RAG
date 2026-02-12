-- Enable Realtime on source_documents for RAG Worker
-- This is required for the worker to receive INSERT/UPDATE events

-- 1. Set REPLICA IDENTITY to FULL for UPDATE events to include old row data
ALTER TABLE public.source_documents REPLICA IDENTITY FULL;

-- 2. Add to Realtime publication (guard against already added)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_publication_tables 
        WHERE pubname = 'supabase_realtime' 
        AND schemaname = 'public' 
        AND tablename = 'source_documents'
    ) THEN
        ALTER PUBLICATION supabase_realtime ADD TABLE public.source_documents;
    END IF;
END $$;
