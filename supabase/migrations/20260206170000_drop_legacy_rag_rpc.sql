-- Migration: Drop Legacy RAG RPC (Phase 6)
-- Description: Removes the deprecated monolith RPC 'hybrid_search_document'.
--             Business logic has been successfully migrated to the application layer (TypeScript)
--             using the Strangler Fig pattern (Phases 1-5).
-- Date: 2026-02-06
-- Note: This action is irreversible and disables the SQL-based fallback mechanism.

DROP FUNCTION IF EXISTS public.hybrid_search_document(
  text,              -- query_text
  vector,            -- query_embedding
  double precision,  -- match_threshold
  integer,           -- match_count
  uuid               -- filter_source_id
);

-- Note: Underlying tables (content_chunks) and indexes remain intact.
-- Only the orchestration logic is removed from the database.
