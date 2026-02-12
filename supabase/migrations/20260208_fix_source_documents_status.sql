-- Migration: Fix Source Documents Status Constraint
-- Description: Updates the 'source_documents_status_check' constraint to include 'failed', 'processed', and 'error_sending_to_queue'
--              to align with TypeScript definitions and 'error'/'ready' for backward compatibility.
-- Date: 2026-02-08

BEGIN;

-- 1. Drop existing constraint
ALTER TABLE public.source_documents DROP CONSTRAINT IF EXISTS source_documents_status_check;

-- 2. Add updated constraint
-- Includes:
--   - 'queued', 'processing' (Common)
--   - 'ready' (DB legacy), 'processed' (TS equivalence)
--   - 'error' (DB legacy), 'failed' (TS new), 'error_sending_to_queue' (TS specific)
ALTER TABLE public.source_documents 
ADD CONSTRAINT source_documents_status_check 
CHECK (status IN (
    'queued', 
    'processing', 
    'ready', 
    'processed', 
    'error', 
    'failed', 
    'error_sending_to_queue'
));

COMMIT;
