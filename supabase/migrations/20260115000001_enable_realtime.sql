-- Migration: Enable Realtime for Live Dashboard
-- Date: 2026-01-15
-- Description: Adds exam_attempts to the supabase_realtime publication to enable live monitoring.
-- Fixed: Uses idempotent logic to avoid "relation already exists" errors.

DO $$
BEGIN
    -- 1. Ensure publication exists
    IF NOT EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
        CREATE PUBLICATION supabase_realtime;
    END IF;

    -- 2. Add table to publication safely
    -- This check prevents ERROR: 42710 if the table is already added
    IF NOT EXISTS (
        SELECT 1
        FROM pg_publication_tables
        WHERE pubname = 'supabase_realtime'
        AND schemaname = 'public'
        AND tablename = 'exam_attempts'
    ) THEN
        ALTER PUBLICATION supabase_realtime ADD TABLE public.exam_attempts;
    END IF;
END;
$$;
