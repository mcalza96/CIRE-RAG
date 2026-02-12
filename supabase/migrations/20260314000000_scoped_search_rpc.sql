-- =============================================================================
-- MIGRATION: SCOPED SEARCH RPC
-- Description: Adds a remote procedure call (RPC) to perform hybrid/semantic search
--              restricted to a specific branch of the curriculum tree (ltree).
-- Date: 2026-03-14
-- =============================================================================

BEGIN;

-- Function: match_chunks_scoped
-- Performs vector similarity search with optional ltree-based scoping.
--
-- Parameters:
--   query_embedding: The vector representation of the user query.
--   match_threshold: Minimum similarity score (0.0 to 1.0) to return a match.
--   match_count: Maximum number of results to return.
--   filter_course_id: Mandatory course context.
--   filter_node_path: (Optional) ltree path to restrict search (e.g. 'root.unit1').
--                     If NULL, searches the entire course.

CREATE OR REPLACE FUNCTION public.match_chunks_scoped(
    query_embedding vector(1536),
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
        -- Vector Similarity Threshold
        AND (1 - (kc.embedding <=> query_embedding)) > match_threshold
        -- Optional Scope Filtering
        -- If filter_node_path is provided, we check if chunk_path is a descendant (<@) 
        -- of that path. The GIST index on chunk_path will optimize this.
        AND (
            filter_node_path IS NULL 
            OR 
            kc.chunk_path <@ filter_node_path
        )
    ORDER BY kc.embedding <=> query_embedding ASC -- Closest distance first
    LIMIT match_count;
END;
$$ SECURITY DEFINER SET search_path = public, extensions;

-- Grant execution to authenticated users
GRANT EXECUTE ON FUNCTION public.match_chunks_scoped(vector(1536), FLOAT, INT, UUID, ltree) TO authenticated;
GRANT EXECUTE ON FUNCTION public.match_chunks_scoped(vector(1536), FLOAT, INT, UUID, ltree) TO service_role;

COMMIT;
