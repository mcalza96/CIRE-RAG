-- Migration: Add Ingestion Control Columns
-- Description: Adds columns to track processing state and ensure idempotency.
-- Date: 2026-02-10

BEGIN;

-- 1. Add columns
ALTER TABLE public.source_documents 
ADD COLUMN IF NOT EXISTS processing_started_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS worker_id UUID,
ADD COLUMN IF NOT EXISTS content_hash TEXT;

-- 2. Add unique index for idempotency
-- We use a unique index on content_hash to prevent duplicate uploads of the same file.
CREATE UNIQUE INDEX IF NOT EXISTS source_documents_content_hash_idx ON public.source_documents (content_hash);

COMMIT;
