-- =============================================================================
-- MIGRATION: FIX EMBEDDING DIMENSIONS (768 for Gemini text-embedding-004)
-- Date: 2026-03-05
-- =============================================================================

BEGIN;

-- 1. Alter content_chunks table to 768 dimensions
-- This will delete existing embeddings! (Necessary due to dimension change)
ALTER TABLE public.content_chunks 
ALTER COLUMN embedding TYPE vector(768);

-- 2. Drop and Re-create HNSW Index with new dimensions
DROP INDEX IF EXISTS idx_content_chunks_embedding;
CREATE INDEX IF NOT EXISTS idx_content_chunks_embedding 
ON public.content_chunks USING hnsw (embedding vector_cosine_ops);

-- 3. Update hybrid_search RPC to use 768d
CREATE OR REPLACE FUNCTION public.hybrid_search(
    query_text TEXT,
    query_embedding vector(768),
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
        LIMIT match_count * 2
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

-- Grant execute permission to authenticated users (redundant but safe)
GRANT EXECUTE ON FUNCTION public.hybrid_search(TEXT, vector(768), FLOAT, INT, UUID) TO authenticated;
GRANT EXECUTE ON FUNCTION public.hybrid_search(TEXT, vector(768), FLOAT, INT, UUID) TO service_role;

COMMIT;
