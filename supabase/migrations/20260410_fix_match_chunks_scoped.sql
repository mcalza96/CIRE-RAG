-- =============================================================================
-- MIGRATION: Update match_chunks_scoped to 1024d
-- =============================================================================
-- Description: Standardizes match_chunks_scoped to use 1024-dimensional 
--              embeddings (Jina v3) to match the Unified Vector Space.
-- =============================================================================

BEGIN;

CREATE OR REPLACE FUNCTION public.match_chunks_scoped(
    query_embedding vector(1024), -- Updated from 1536
    match_threshold FLOAT,
    match_count INT,
    filter_course_id UUID,
    filter_node_path ltree DEFAULT NULL
)
RETURNS TABLE (
    id UUID,
    node_id UUID,
    chunk_path ltree,
    content TEXT,
    similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        kc.id,
        kc.node_id,
        kc.chunk_path,
        kc.content,
        (1 - (kc.embedding <=> query_embedding)) AS similarity
    FROM public.knowledge_chunks kc
    JOIN public.course_nodes cn ON cn.id = kc.node_id
    WHERE 
        cn.course_id = filter_course_id
        AND (1 - (kc.embedding <=> query_embedding)) > match_threshold
        AND (
            filter_node_path IS NULL 
            OR 
            kc.chunk_path <@ filter_node_path
        )
    ORDER BY kc.embedding <=> query_embedding ASC
    LIMIT match_count;
END;
$$ SECURITY DEFINER SET search_path = public, extensions;

COMMIT;
