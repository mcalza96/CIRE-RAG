-- =============================================================================
-- MIGRATION: FIX RAG LEAK & FILTERS (Comprehensive Patch)
-- =============================================================================
-- Description: Updates match_vectors_graph_guided to support:
--   1. Global Content Isolation (p_allowed_global_ids)
--   2. Authority Level Filtering (p_filter_authority)
--   3. Institutional Isolation (p_filter_institutional)
--
-- Date: 2026-05-01 (Hotfix Update)
-- Author: Principal DevOps
-- =============================================================================

BEGIN;

CREATE OR REPLACE FUNCTION public.match_vectors_graph_guided(
    query_embedding vector(1024),
    match_threshold float DEFAULT 0.5,
    match_count int DEFAULT 10,
    p_tenant_id uuid DEFAULT NULL,
    p_allowed_global_ids uuid[] DEFAULT NULL,
    p_filter_authority text DEFAULT NULL, -- 'hard_constraint', 'advisory', etc.
    p_filter_institutional boolean DEFAULT FALSE -- If true, ignore global content
)
RETURNS TABLE (
    id uuid,
    content text,
    node_type text,
    title text,
    similarity float,
    retrieval_method text,
    graph_depth int,
    boosted_score float
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    VETA_BOOST float := 1.5;
    REQUIERE_BOOST float := 1.2;
    ANCHOR_COUNT int := 15;
BEGIN
    RETURN QUERY
    WITH anchor_nodes AS (
        SELECT
            n.id,
            n.title,
            n.content,
            n.node_type::text,
            (1 - (n.embedding <=> query_embedding)) AS similarity
        FROM public.regulatory_nodes n
        WHERE n.tenant_id = p_tenant_id
          AND n.embedding IS NOT NULL
          AND (1 - (n.embedding <=> query_embedding)) > match_threshold
          
          -- CRITICAL FIX 1: ESTANQUEIDAD GLOBAL
          AND (
              -- If filtering by institutional only, skip allowed_global logic unless strictly needed
              (p_filter_institutional IS TRUE AND (n.metadata->>'is_global')::boolean IS NOT TRUE)
              OR
              (
                 p_filter_institutional IS FALSE AND (
                    p_allowed_global_ids IS NULL 
                    OR cardinality(p_allowed_global_ids) = 0 
                    OR n.id = ANY(p_allowed_global_ids) 
                    OR (n.metadata->>'source_id')::uuid = ANY(p_allowed_global_ids)
                 )
              )
          )

          -- CRITICAL FIX 2: AUTHORITY FILTER
          AND (
              p_filter_authority IS NULL 
              OR n.metadata->>'enforcement_level' = p_filter_authority
          )

        ORDER BY n.embedding <=> query_embedding
        LIMIT ANCHOR_COUNT
    ),

    graph_expansion AS (
        SELECT
            an.id AS anchor_id,
            e.target_id AS expanded_node_id,
            e.edge_type::text AS relation_type,
            e.weight AS edge_weight,
            1 AS depth
        FROM anchor_nodes an
        JOIN public.regulatory_edges e ON e.source_id = an.id
        WHERE e.edge_type IN ('VETA', 'REQUIERE')

        UNION ALL

        SELECT
            ge.anchor_id,
            e2.target_id AS expanded_node_id,
            e2.edge_type::text AS relation_type,
            ge.edge_weight * e2.weight AS edge_weight,
            2 AS depth
        FROM (
            SELECT DISTINCT ge_inner.anchor_id, ge_inner.expanded_node_id, ge_inner.edge_weight
            FROM (
                SELECT
                    an.id AS anchor_id,
                    e.target_id AS expanded_node_id,
                    e.weight AS edge_weight
                FROM anchor_nodes an
                JOIN public.regulatory_edges e ON e.source_id = an.id
                WHERE e.edge_type IN ('VETA', 'REQUIERE')
            ) ge_inner
        ) ge
        JOIN public.regulatory_edges e2 ON e2.source_id = ge.expanded_node_id
        WHERE e2.edge_type IN ('VETA', 'REQUIERE')
          AND e2.target_id NOT IN (SELECT id FROM anchor_nodes)
    ),

    combined_results AS (
        SELECT
            an.id,
            an.title,
            an.content,
            an.node_type,
            an.similarity,
            'vector'::text AS retrieval_method,
            0 AS graph_depth,
            an.similarity AS raw_score
        FROM anchor_nodes an

        UNION ALL

        SELECT
            n.id,
            n.title,
            n.content,
            n.node_type::text,
            (SELECT MAX(similarity) FROM anchor_nodes) * ge.edge_weight AS similarity,
            CASE 
                WHEN ge.relation_type = 'VETA' THEN 'graph_veta'
                ELSE 'graph_requiere'
            END AS retrieval_method,
            ge.depth AS graph_depth,
            CASE
                WHEN ge.relation_type = 'VETA' THEN 
                    (SELECT MAX(similarity) FROM anchor_nodes) * ge.edge_weight * VETA_BOOST
                ELSE 
                    (SELECT MAX(similarity) FROM anchor_nodes) * ge.edge_weight * REQUIERE_BOOST
            END AS raw_score
        FROM graph_expansion ge
        JOIN public.regulatory_nodes n ON n.id = ge.expanded_node_id
        WHERE n.tenant_id = p_tenant_id
    ),

    deduplicated AS (
        SELECT DISTINCT ON (cr.id)
            cr.id,
            cr.content,
            cr.node_type,
            cr.title,
            cr.similarity,
            cr.retrieval_method,
            cr.graph_depth,
            cr.raw_score AS boosted_score
        FROM combined_results cr
        ORDER BY cr.id, cr.raw_score DESC
    )

    SELECT
        d.id,
        d.content,
        d.node_type,
        d.title,
        d.similarity,
        d.retrieval_method,
        d.graph_depth,
        d.boosted_score
    FROM deduplicated d
    ORDER BY d.boosted_score DESC
    LIMIT match_count;
END;
$$;

GRANT EXECUTE ON FUNCTION public.match_vectors_graph_guided(vector(1024), float, int, uuid, uuid[], text, boolean) TO authenticated;
GRANT EXECUTE ON FUNCTION public.match_vectors_graph_guided(vector(1024), float, int, uuid, uuid[], text, boolean) TO service_role;

COMMIT;
