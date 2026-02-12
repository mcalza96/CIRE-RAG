-- =============================================================================
-- MIGRATION: SEMANTIC DISCOVERY ENGINE (Phase 2)
-- =============================================================================
-- Description: Implements a hybrid (semantic + keyword) search motor for 
--              the global assets catalog.
--
-- Optimization: Uses HNSW index for vector similarity and GIN for keyword search.
-- =============================================================================

BEGIN;

-- 1. ENHANCE global_assets TABLE
-- Add FTS column for keyword search optimization
ALTER TABLE public.global_assets 
ADD COLUMN IF NOT EXISTS fts tsvector 
GENERATED ALWAYS AS (
    to_tsvector('spanish', coalesce(title, '') || ' ' || coalesce(description, ''))
) STORED;

-- 2. CREATE INDEXES
-- Index for keyword search (Spanish)
CREATE INDEX IF NOT EXISTS idx_global_assets_fts ON public.global_assets USING gin(fts);

-- Index for vector search (HNSW for Jina 1024d)
-- Note: cosine similarity is standard for retrieval
CREATE INDEX IF NOT EXISTS idx_global_assets_embedding 
ON public.global_assets USING hnsw (embedding vector_cosine_ops);

-- 3. RPC FUNCTION: search_global_catalog
-- This function performs hybrid search and returns ONLY metadata (no content/vectors).
CREATE OR REPLACE FUNCTION public.search_global_catalog(
    p_query_embedding vector(1024),
    p_query_text TEXT DEFAULT NULL,
    p_match_threshold FLOAT DEFAULT 0.3,
    p_match_count INT DEFAULT 10,
    p_filter_category TEXT DEFAULT NULL
)
RETURNS TABLE (
    id UUID,
    title TEXT,
    description TEXT,
    category TEXT,
    tags TEXT[],
    similarity FLOAT
) 
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RETURN QUERY
    WITH semantic_matches AS (
        -- Step 1: Semantic similarity search
        SELECT 
            ga.id,
            (1 - (ga.embedding <=> p_query_embedding)) AS v_similarity
        FROM public.global_assets ga
        WHERE (ga.embedding <=> p_query_embedding) < (1 - p_match_threshold)
          AND (p_filter_category IS NULL OR ga.category = p_filter_category)
        ORDER BY ga.embedding <=> p_query_embedding
        LIMIT p_match_count * 2 -- Over-fetch for hybrid merging
    ),
    keyword_matches AS (
        -- Step 2: Full-text search (if query_text is provided)
        SELECT 
            ga.id,
            ts_rank_cd(ga.fts, to_tsquery('spanish', websearch_to_tsquery('spanish', p_query_text)::text)) AS fts_rank
        FROM public.global_assets ga
        WHERE ga.fts @@ websearch_to_tsquery('spanish', p_query_text)
          AND (p_filter_category IS NULL OR ga.category = p_filter_category)
        LIMIT p_match_count * 2
    )
    -- Step 3: Combine and return metadata
    SELECT 
        ga.id,
        ga.title,
        ga.description,
        ga.category,ga.tags,
        COALESCE(sm.v_similarity, 0) + (COALESCE(km.fts_rank, 0) * 0.1) AS combined_similarity
    FROM public.global_assets ga
    LEFT JOIN semantic_matches sm ON ga.id = sm.id
    LEFT JOIN keyword_matches km ON ga.id = km.id
    WHERE sm.id IS NOT NULL OR km.id IS NOT NULL
    ORDER BY combined_similarity DESC
    LIMIT p_match_count;
END;
$$;

-- Documentation
COMMENT ON FUNCTION public.search_global_catalog IS 'Hybrid discovery engine for global assets. Efficiently merges vector similarity with keyword relevance.';

-- Grant access to authenticated users
GRANT EXECUTE ON FUNCTION public.search_global_catalog TO authenticated;
GRANT EXECUTE ON FUNCTION public.search_global_catalog TO service_role;

COMMIT;
