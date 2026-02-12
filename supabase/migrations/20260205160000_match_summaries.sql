-- =============================================================================
-- MIGRATION: match_summaries RPC
-- =============================================================================
-- Description: Performs semantic search specifically on RAPTOR summary nodes
--               (level > 0) in the regulatory_nodes table.
--
-- This is part of the hierarchical retrieval system to ensure broad queries
-- can hit summarized knowledge abstractions.
-- =============================================================================

BEGIN;

CREATE OR REPLACE FUNCTION public.match_summaries(
    query_embedding vector(1024),
    match_threshold float DEFAULT 0.5,
    match_count int DEFAULT 10,
    p_tenant_id uuid DEFAULT NULL
)
RETURNS TABLE (
    id uuid,
    content text,
    title text,
    level int,
    similarity float,
    properties jsonb
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RETURN QUERY
    SELECT
        n.id,
        n.content,
        n.title,
        n.level,
        (1 - (n.embedding <=> query_embedding)) AS similarity,
        n.properties
    FROM public.regulatory_nodes n
    WHERE n.tenant_id = p_tenant_id
      AND n.level > 0
      AND n.embedding IS NOT NULL
      AND (1 - (n.embedding <=> query_embedding)) > match_threshold
    ORDER BY n.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- Permissions
GRANT EXECUTE ON FUNCTION public.match_summaries(vector(1024), float, int, uuid)
TO authenticated;

GRANT EXECUTE ON FUNCTION public.match_summaries(vector(1024), float, int, uuid)
TO service_role;

-- Documentation
COMMENT ON FUNCTION public.match_summaries IS
'Performs semantic search on RAPTOR hierarchical summaries (regulatory_nodes where level > 0).';

COMMIT;
