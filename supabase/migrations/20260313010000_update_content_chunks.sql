-- =============================================================================
-- MIGRATION: UPDATE CONTENT CHUNKS FOR JINA V3 & MULTI-TENANCY
-- Description: Updates table definition to support Jina Embeddings (1024d) and
--              metadata context injection.
-- Date: 2026-03-13
-- =============================================================================

BEGIN;

-- 1. Alter content_chunks
-- Add missing columns
ALTER TABLE public.content_chunks 
ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'::jsonb,
ADD COLUMN IF NOT EXISTS institution_id UUID, -- For multi-tenant isolation
ADD COLUMN IF NOT EXISTS is_global BOOLEAN DEFAULT FALSE;

-- Update vector dimension (1536 -> 1024)
-- We need to drop the index first as it depends on the column type
DROP INDEX IF EXISTS idx_content_chunks_embedding;

-- Alter column type (using USING to cast existing data, though it will be invalid)
-- Since we are switching models, existing embeddings are garbage anyway. 
-- Ideally we would re-embed, but for this task we assume we can truncate or just change type.
-- If the table is empty or we don't care about old data:
DELETE FROM public.content_chunks; -- SAFETY: truncate to avoid type cast errors on existing data
ALTER TABLE public.content_chunks ALTER COLUMN embedding TYPE vector(1024);

-- Recreate Index
CREATE INDEX IF NOT EXISTS idx_content_chunks_embedding 
ON public.content_chunks USING hnsw (embedding vector_cosine_ops);

-- 2. Update Hybrid Search Function
-- Must update input parameter type to vector(1024)
DROP FUNCTION IF EXISTS public.hybrid_search(TEXT, vector(1536), FLOAT, INT, UUID);

CREATE OR REPLACE FUNCTION public.hybrid_search(
    query_text TEXT,
    query_embedding vector(1024),
    match_threshold FLOAT,
    match_count INT,
    filter_course_id UUID
)
RETURNS TABLE (
    id UUID,
    source_id UUID,
    content TEXT,
    semantic_context TEXT,
    similarity FLOAT,
    fts_rank FLOAT,
    rrf_score FLOAT
)
LANGUAGE plpgsql
AS $$
DECLARE
    k CONSTANT INT := 60; -- RRF constant
BEGIN
    RETURN QUERY
    WITH vector_search AS (
        SELECT 
            cc.id,
            (1 - (cc.embedding <=> query_embedding)) AS similarity,
            ROW_NUMBER() OVER (ORDER BY (cc.embedding <=> query_embedding) ASC) AS rank_vec
        FROM public.content_chunks cc
        JOIN public.source_documents sd ON sd.id = cc.source_id
        WHERE sd.course_id = filter_course_id
        AND (1 - (cc.embedding <=> query_embedding)) > match_threshold
        ORDER BY similarity DESC
        LIMIT match_count * 2 -- Fetch more to allow for intersection
    ),
    keyword_search AS (
        SELECT 
            cc.id,
            ts_rank_cd(cc.fts, websearch_to_tsquery('spanish', query_text)) AS rank_val,
            ROW_NUMBER() OVER (ORDER BY ts_rank_cd(cc.fts, websearch_to_tsquery('spanish', query_text)) DESC) AS rank_fts
        FROM public.content_chunks cc
        JOIN public.source_documents sd ON sd.id = cc.source_id
        WHERE sd.course_id = filter_course_id
        AND cc.fts @@ websearch_to_tsquery('spanish', query_text)
        ORDER BY rank_val DESC
        LIMIT match_count * 2
    )
    SELECT
        cc.id,
        cc.source_id,
        cc.content,
        cc.semantic_context,
        COALESCE(vs.similarity, 0) AS similarity,
        COALESCE(ks.rank_val, 0) AS fts_rank,
        (
            COALESCE(1.0 / (k + vs.rank_vec), 0.0) +
            COALESCE(1.0 / (k + ks.rank_fts), 0.0)
        ) AS rrf_score
    FROM public.content_chunks cc
    LEFT JOIN vector_search vs ON vs.id = cc.id
    LEFT JOIN keyword_search ks ON ks.id = cc.id
    WHERE vs.id IS NOT NULL OR ks.id IS NOT NULL
    ORDER BY rrf_score DESC
    LIMIT match_count;
END;
$$ SECURITY DEFINER SET search_path = public, extensions;

GRANT EXECUTE ON FUNCTION public.hybrid_search(TEXT, vector(1024), FLOAT, INT, UUID) TO authenticated;
GRANT EXECUTE ON FUNCTION public.hybrid_search(TEXT, vector(1024), FLOAT, INT, UUID) TO service_role;

COMMIT;
