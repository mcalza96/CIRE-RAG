-- Migration: Atomic RAG Primitives (Phase 1)
-- Description: Adds 'search_vectors_only' and 'search_fts_only' for atomic retrieval.
-- Date: 2026-02-06
-- Note: These are designed to be "dumb" primitives that return raw results for application-layer fusion.

-- 1. Atomic Vector Search (No RRF, No Metadata Filtering beyond Source)
-- Note: Returns distance as similarity (1 - distance) for consistency.
CREATE OR REPLACE FUNCTION public.search_vectors_only(
  query_embedding vector,
  match_threshold double precision,
  match_count integer,
  source_ids uuid[]
)
RETURNS TABLE(
  id uuid,
  content text,
  metadata jsonb,
  similarity double precision
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'extensions'
AS $$
BEGIN
    RETURN QUERY
    SELECT 
        cc.id,
        cc.content,
        jsonb_build_object(
            'semantic_context', cc.semantic_context,
            'file_page_number', cc.file_page_number,
            'source_id', cc.source_id,
            -- Preserve legacy fields just in case, or add more here if needed
            'source_file', 'unknown' -- Will be populated by join in future or if column exists
        ) AS metadata,
        (1 - (cc.embedding <=> query_embedding)) AS similarity
    FROM public.content_chunks cc
    WHERE cc.source_id = ANY(source_ids)
    AND (1 - (cc.embedding <=> query_embedding)) > match_threshold
    ORDER BY similarity DESC
    LIMIT match_count;
END;
$$;

-- 2. Atomic FTS Search (No RRF)
-- Note: Replicates 'websearch_to_tsquery' logic with 'spanish' config from legacy RPC.
CREATE OR REPLACE FUNCTION public.search_fts_only(
  query_text text,
  match_count integer,
  source_ids uuid[]
)
RETURNS TABLE(
  id uuid,
  content text,
  metadata jsonb,
  rank double precision
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'extensions'
AS $$
BEGIN
    RETURN QUERY
    SELECT 
        cc.id,
        cc.content,
        jsonb_build_object(
            'semantic_context', cc.semantic_context,
            'file_page_number', cc.file_page_number,
            'source_id', cc.source_id
        ) AS metadata,
        ts_rank_cd(cc.fts, websearch_to_tsquery('spanish', query_text)) AS rank
    FROM public.content_chunks cc
    WHERE cc.source_id = ANY(source_ids)
    AND cc.fts @@ websearch_to_tsquery('spanish', query_text)
    ORDER BY rank DESC
    LIMIT match_count;
END;
$$;
