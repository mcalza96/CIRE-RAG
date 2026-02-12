-- =============================================================================
-- MIGRATION: BICAMERAL SEARCH RPC (V3)
-- Description: Advances match_vectors to support arbitrary metadata filtering
--              preserving HNSW performance.
-- Date: 2026-02-07
-- =============================================================================

BEGIN;

CREATE OR REPLACE FUNCTION public.match_vectors_v3(
    query_embedding vector(1024), -- Unified length for Jina v3
    match_threshold float DEFAULT 0.5,
    match_count int DEFAULT 10,
    p_tenant_id uuid DEFAULT NULL,
    p_filters jsonb DEFAULT '{}'::jsonb
)
RETURNS TABLE (
    id uuid,
    source_id uuid,
    content text,
    similarity float,
    metadata jsonb,
    source_layer text
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    RETURN QUERY
    SELECT 
        cc.id,
        cc.source_id,
        cc.content,
        (1 - (cc.embedding <=> query_embedding)) as similarity,
        cc.metadata,
        CASE 
            WHEN cc.is_global THEN 'global'
            ELSE 'local'
        END as source_layer
    FROM public.content_chunks cc
    WHERE 1 - (cc.embedding <=> query_embedding) > match_threshold
    AND (p_tenant_id IS NULL OR cc.institution_id = p_tenant_id OR cc.is_global = true)
    AND (
        -- Dynamic Filtering Logic
        (NOT (p_filters ? 'source_ids') OR (cc.source_id::text = ANY(ARRAY(SELECT jsonb_array_elements_text(p_filters->'source_ids')))))
        AND
        (NOT (p_filters ? 'user_id') OR (cc.metadata->>'user_id' = p_filters->>'user_id'))
    )
    ORDER BY cc.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

GRANT EXECUTE ON FUNCTION public.match_vectors_v3(vector, float, int, uuid, jsonb) TO authenticated;

COMMIT;
