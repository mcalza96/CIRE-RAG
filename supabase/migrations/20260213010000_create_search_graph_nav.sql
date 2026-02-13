CREATE OR REPLACE FUNCTION public.search_graph_nav(
    query_embedding vector,
    p_tenant_id uuid,
    match_threshold double precision DEFAULT 0.25,
    limit_count int DEFAULT 12,
    max_hops int DEFAULT 2,
    decay_factor double precision DEFAULT 0.82,
    filter_entity_types text[] DEFAULT NULL,
    filter_relation_types text[] DEFAULT NULL
)
RETURNS TABLE (
    entity_id uuid,
    entity_name text,
    entity_type text,
    entity_description text,
    similarity double precision,
    hop_depth int,
    path_ids uuid[]
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
WITH RECURSIVE
anchor_nodes AS (
    SELECT
        e.id,
        e.name,
        e.type,
        e.description,
        (1 - (e.embedding <=> query_embedding))::double precision AS similarity,
        0 AS hop_depth,
        ARRAY[e.id]::uuid[] AS path_ids
    FROM public.knowledge_entities e
    WHERE e.tenant_id = p_tenant_id
      AND e.embedding IS NOT NULL
      AND 1 - (e.embedding <=> query_embedding) >= match_threshold
      AND (filter_entity_types IS NULL OR e.type = ANY(filter_entity_types))
    ORDER BY e.embedding <=> query_embedding
    LIMIT GREATEST(limit_count, 1)
),
traversal AS (
    SELECT
        a.id,
        a.name,
        a.type,
        a.description,
        a.similarity,
        a.hop_depth,
        a.path_ids
    FROM anchor_nodes a

    UNION ALL

    SELECT
        n.id,
        n.name,
        n.type,
        n.description,
        (t.similarity * GREATEST(LEAST(decay_factor, 1.0), 0.0))::double precision AS similarity,
        t.hop_depth + 1,
        (t.path_ids || n.id)::uuid[] AS path_ids
    FROM traversal t
    JOIN public.knowledge_relations r
      ON r.tenant_id = p_tenant_id
     AND (r.source_entity_id = t.id OR r.target_entity_id = t.id)
    JOIN public.knowledge_entities n
      ON n.tenant_id = p_tenant_id
     AND n.id = CASE
            WHEN r.source_entity_id = t.id THEN r.target_entity_id
            ELSE r.source_entity_id
        END
    WHERE t.hop_depth < GREATEST(max_hops, 0)
      AND n.id <> ALL(t.path_ids)
      AND (filter_entity_types IS NULL OR n.type = ANY(filter_entity_types))
      AND (filter_relation_types IS NULL OR r.relation_type = ANY(filter_relation_types))
)
SELECT DISTINCT ON (t.id)
    t.id AS entity_id,
    t.name AS entity_name,
    t.type AS entity_type,
    t.description AS entity_description,
    t.similarity,
    t.hop_depth,
    t.path_ids
FROM traversal t
ORDER BY t.id, t.similarity DESC
LIMIT GREATEST(limit_count, 1) * 3;
$$;

REVOKE ALL ON FUNCTION public.search_graph_nav(vector, uuid, double precision, int, int, double precision, text[], text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.search_graph_nav(vector, uuid, double precision, int, int, double precision, text[], text[]) TO authenticated;
GRANT EXECUTE ON FUNCTION public.search_graph_nav(vector, uuid, double precision, int, int, double precision, text[], text[]) TO service_role;
