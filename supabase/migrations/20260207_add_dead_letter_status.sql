-- Migration: Add 'dead_letter' to Source Documents Status
-- Description: Updates the 'source_documents_status_check' constraint to include 'dead_letter' which is used by the worker for failed retries.
-- Date: 2026-02-07

BEGIN;

-- 1. Drop existing constraint
ALTER TABLE public.source_documents DROP CONSTRAINT IF EXISTS source_documents_status_check;

-- 2. Add updated constraint
-- Includes all previous values plus 'dead_letter'
ALTER TABLE public.source_documents 
ADD CONSTRAINT source_documents_status_check 
CHECK (status IN (
    'queued', 
    'processing', 
    'ready', 
    'processed', 
    'error', 
    'failed', 
    'error_sending_to_queue',
    'dead_letter'
));

COMMIT;
