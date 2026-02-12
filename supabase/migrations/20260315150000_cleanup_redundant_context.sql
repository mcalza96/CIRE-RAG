
-- =============================================================================
-- MIGRATION: CLEAN REDUNDANT CONTEXT
-- Description: Trims the "CONTEXTO DOCUMENTO: ... \n\n" prefix from content_chunks.
--              This prefix is now handled at the prompt level to save tokens.
-- Date: 2026-03-15
-- =============================================================================

BEGIN;

-- We use regex to remove everything from 'CONTEXTO DOCUMENTO:' until the first double newline '\n\n'
-- This regex targets: 'CONTEXTO DOCUMENTO:' followed by any characters (non-greedy) until '\n\n'
-- Then it replaces it with an empty string.
UPDATE public.content_chunks
SET content = regexp_replace(content, '^CONTEXTO DOCUMENTO:.*?\n\n', '', 's')
WHERE content LIKE 'CONTEXTO DOCUMENTO:%';

-- Verify counts (optional, check in dashboard after run)
-- SELECT count(*) FROM public.content_chunks WHERE content LIKE 'CONTEXTO DOCUMENTO:%';

COMMIT;
