BEGIN;

CREATE OR REPLACE FUNCTION public.match_knowledge_entities(
    query_embedding vector(1536),
    p_tenant_id uuid,
    match_count int DEFAULT 8,
    match_threshold float DEFAULT 0.72
)
RETURNS TABLE (
    id uuid,
    name text,
    type text,
    description text,
    similarity float
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    RETURN QUERY
    SELECT
        e.id,
        e.name,
        e.type,
        e.description,
        (1 - (e.embedding <=> query_embedding))::float AS similarity
    FROM public.knowledge_entities e
    WHERE e.tenant_id = p_tenant_id
      AND e.embedding IS NOT NULL
      AND (1 - (e.embedding <=> query_embedding)) >= match_threshold
    ORDER BY e.embedding <=> query_embedding
    LIMIT GREATEST(match_count, 1);
END;
$$;

CREATE OR REPLACE FUNCTION public.match_knowledge_communities(
    query_embedding vector(1536),
    p_tenant_id uuid,
    p_level int DEFAULT 0,
    match_count int DEFAULT 5,
    match_threshold float DEFAULT 0.25
)
RETURNS TABLE (
    id uuid,
    community_id int,
    summary text,
    members jsonb,
    similarity float
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    RETURN QUERY
    SELECT
        c.id,
        c.community_id,
        c.summary,
        c.members,
        (1 - (c.embedding <=> query_embedding))::float AS similarity
    FROM public.knowledge_communities c
    WHERE c.tenant_id = p_tenant_id
      AND c.level = p_level
      AND c.embedding IS NOT NULL
      AND (1 - (c.embedding <=> query_embedding)) >= match_threshold
    ORDER BY c.embedding <=> query_embedding
    LIMIT GREATEST(match_count, 1);
END;
$$;

REVOKE ALL ON FUNCTION public.match_knowledge_entities(vector, uuid, int, float) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.match_knowledge_communities(vector, uuid, int, int, float) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION public.match_knowledge_entities(vector, uuid, int, float) TO authenticated;
GRANT EXECUTE ON FUNCTION public.match_knowledge_entities(vector, uuid, int, float) TO service_role;
GRANT EXECUTE ON FUNCTION public.match_knowledge_communities(vector, uuid, int, int, float) TO authenticated;
GRANT EXECUTE ON FUNCTION public.match_knowledge_communities(vector, uuid, int, int, float) TO service_role;

COMMIT;
