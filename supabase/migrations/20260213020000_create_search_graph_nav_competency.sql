-- Optional overload for TeacherOS competency schema (schema.sql)
-- Keeps planner-controlled traversal on competency_nodes / competency_edges.

CREATE OR REPLACE FUNCTION public.search_graph_nav(
    query_embedding vector(1536),
    match_threshold double precision DEFAULT 0.5,
    limit_count int DEFAULT 10,
    max_hops int DEFAULT 2,
    decay_factor double precision DEFAULT 0.75,
    filter_node_types public.node_type_enum[] DEFAULT NULL,
    filter_relation_types public.relation_type_enum[] DEFAULT NULL
)
RETURNS TABLE (
    id uuid,
    title text,
    description text,
    node_type public.node_type_enum,
    similarity double precision,
    hop_depth int,
    path_info jsonb
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
WITH RECURSIVE
anchor_nodes AS (
    SELECT
        n.id,
        n.title,
        n.description,
        n.node_type,
        (1 - (n.embedding <=> query_embedding))::double precision AS similarity,
        0 AS hop_depth,
        jsonb_build_array(jsonb_build_object('id', n.id, 'title', n.title)) AS path_info
    FROM public.competency_nodes n
    WHERE n.embedding IS NOT NULL
      AND (1 - (n.embedding <=> query_embedding) >= match_threshold)
      AND (filter_node_types IS NULL OR n.node_type = ANY(filter_node_types))
    ORDER BY n.embedding <=> query_embedding
    LIMIT GREATEST(limit_count, 1)
),
graph_traversal AS (
    SELECT
        a.id,
        a.title,
        a.description,
        a.node_type,
        a.similarity,
        a.hop_depth,
        a.path_info
    FROM anchor_nodes a

    UNION ALL

    SELECT
        target.id,
        target.title,
        target.description,
        target.node_type,
        (origin.similarity * GREATEST(LEAST(decay_factor, 1.0), 0.0))::double precision,
        origin.hop_depth + 1,
        origin.path_info || jsonb_build_array(
            jsonb_build_object('edge', e.relation_type, 'id', target.id, 'title', target.title)
        )
    FROM graph_traversal origin
    JOIN public.competency_edges e ON e.source_id = origin.id
    JOIN public.competency_nodes target ON target.id = e.target_id
    WHERE origin.hop_depth < GREATEST(max_hops, 0)
      AND (filter_relation_types IS NULL OR e.relation_type = ANY(filter_relation_types))
      AND NOT EXISTS (
          SELECT 1
          FROM jsonb_array_elements(origin.path_info) AS elem
          WHERE (elem->>'id')::uuid = target.id
      )
)
SELECT DISTINCT ON (t.id)
    t.id,
    t.title,
    t.description,
    t.node_type,
    t.similarity,
    t.hop_depth,
    t.path_info
FROM graph_traversal t
ORDER BY t.id, t.similarity DESC
LIMIT GREATEST(limit_count, 1) * 3;
$$;

REVOKE ALL ON FUNCTION public.search_graph_nav(vector, double precision, int, int, double precision, public.node_type_enum[], public.relation_type_enum[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.search_graph_nav(vector, double precision, int, int, double precision, public.node_type_enum[], public.relation_type_enum[]) TO authenticated;
GRANT EXECUTE ON FUNCTION public.search_graph_nav(vector, double precision, int, int, double precision, public.node_type_enum[], public.relation_type_enum[]) TO service_role;
