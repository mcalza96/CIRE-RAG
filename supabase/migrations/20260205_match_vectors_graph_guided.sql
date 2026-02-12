-- =============================================================================
-- MIGRATION: Graph-Guided Vector Search RPC
-- =============================================================================
-- Description: Implements a hybrid retrieval function that combines:
--   1. Anchor Search: Fast vector similarity to find initial matches.
--   2. Graph Expansion: Recursive traversal to find VETA (exceptions) and
--      REQUIERE (dependencies) that MUST be included in the context.
--   3. Score Fusion: Re-ranks results, boosting graph-derived nodes.
--
-- The "Veto Rule":
--   If an Anchor node (e.g., "Regla de Asistencia") has an incoming VETA edge
--   from an Exception node, that Exception MUST appear in the results,
--   ideally BEFORE the original rule (higher rank). This is "Deterministic RAG".
--
-- Date: 2026-02-05
-- Author: CISRE Architecture Team
-- =============================================================================

BEGIN;

-- =============================================================================
-- FUNCTION: match_vectors_graph_guided
-- =============================================================================
CREATE OR REPLACE FUNCTION public.match_vectors_graph_guided(
    query_embedding vector(1024),
    match_threshold float DEFAULT 0.5,
    match_count int DEFAULT 10,
    p_tenant_id uuid DEFAULT NULL
)
RETURNS TABLE (
    id uuid,
    content text,
    node_type text,
    title text,
    similarity float,
    retrieval_method text, -- 'vector', 'graph_veta', 'graph_requiere'
    graph_depth int,
    boosted_score float
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    -- Scoring Boosts: Exceptions (VETA) are critical, dependencies (REQUIERE) are important.
    VETA_BOOST float := 1.5;
    REQUIERE_BOOST float := 1.2;
    ANCHOR_COUNT int := 5; -- Number of anchor nodes to find via vector search
BEGIN
    -- ==========================================================================
    -- STEP 1: ANCHOR SEARCH (Semantic Seed)
    -- Quickly find the top-K most relevant nodes via vector similarity.
    -- ==========================================================================
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
        ORDER BY n.embedding <=> query_embedding
        LIMIT ANCHOR_COUNT
    ),

    -- ==========================================================================
    -- STEP 2: GRAPH EXPANSION (Recursive Traversal)
    -- For each anchor, find connected nodes that MODIFY it (VETA, REQUIERE).
    -- 
    -- CRITICAL INSIGHT: We look for INCOMING edges TO the anchor.
    -- An Exception that VETAs a Rule has an edge: Rule --VETA--> Exception.
    -- Thus, from the Rule (anchor), we follow OUTGOING edges to find the Exception.
    -- Similarly for REQUIERE (dependencies).
    -- However, conceptually, the Exception is what modifies the Rule.
    -- The schema's `source_id -> target_id` means `source VETA target`.
    -- So if Rule A has an Exception B, the edge is A(source) --VETA--> B(target).
    -- To find "what overrides A", we look for edges WHERE source_id = A.id 
    -- AND edge_type = 'VETA'.
    --
    -- This is already implemented in `get_regulatory_path`. But here we want to
    -- inline it for a single, efficient query.
    -- ==========================================================================
    graph_expansion AS (
        SELECT
            an.id AS anchor_id,
            e.target_id AS expanded_node_id,
            e.edge_type::text AS relation_type,
            e.weight AS edge_weight,
            1 AS depth -- Depth 1: Direct connection
        FROM anchor_nodes an
        JOIN public.regulatory_edges e ON e.source_id = an.id
        WHERE e.edge_type IN ('VETA', 'REQUIERE')

        UNION ALL

        -- Depth 2: Follow edges from depth-1 nodes (max 2 hops)
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
          AND e2.target_id NOT IN (SELECT id FROM anchor_nodes) -- Avoid cycles back to anchor
    ),

    -- ==========================================================================
    -- STEP 3: COMBINE & DEDUPLICATE
    -- Merge anchor nodes and expanded nodes, keeping best method/score.
    -- ==========================================================================
    combined_results AS (
        -- Anchor nodes from vector search
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

        -- Expanded nodes from graph traversal
        SELECT
            n.id,
            n.title,
            n.content,
            n.node_type::text,
            -- Calculate a "pseudo-similarity" based on edge weight and boost.
            -- This ensures graph-derived nodes have a comparable score.
            (SELECT MAX(similarity) FROM anchor_nodes) * ge.edge_weight AS similarity,
            CASE 
                WHEN ge.relation_type = 'VETA' THEN 'graph_veta'
                ELSE 'graph_requiere'
            END AS retrieval_method,
            ge.depth AS graph_depth,
            -- Apply boost factor based on relation type
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

    -- Deduplicate: If a node appears via multiple methods, keep the best score.
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

    -- ==========================================================================
    -- STEP 4: FINAL RANKING
    -- Sort by boosted_score so exceptions (VETAs) appear before their source rules.
    -- ==========================================================================
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

-- =============================================================================
-- PERMISSIONS
-- =============================================================================
GRANT EXECUTE ON FUNCTION public.match_vectors_graph_guided(vector(1024), float, int, uuid)
TO authenticated;

GRANT EXECUTE ON FUNCTION public.match_vectors_graph_guided(vector(1024), float, int, uuid)
TO service_role;

-- =============================================================================
-- DOCUMENTATION
-- =============================================================================
COMMENT ON FUNCTION public.match_vectors_graph_guided IS
'Graph-Guided Hybrid Vector Search for Regulatory Knowledge Graph.

Algorithm:
1. Anchor Search: Finds top-K semantically similar nodes via vector search.
2. Graph Expansion: For each anchor, traverses VETA and REQUIERE edges to find
   exceptions and dependencies that MUST be included in RAG context.
3. Score Fusion: Boosts scores for graph-derived nodes (VETA x1.5, REQUIERE x1.2)
   to ensure exceptions appear BEFORE their source rules.

This implements "Deterministic RAG" where exceptions explicitly override rules.

Parameters:
- query_embedding: The vector representation of the user query (1024d for Jina v3).
- match_threshold: Minimum similarity for anchor nodes (default 0.5).
- match_count: Maximum results to return (default 10).
- p_tenant_id: Multi-tenant isolation filter. REQUIRED for security.

Returns:
- id, content, node_type, title: Basic node info.
- similarity: Raw vector similarity (for anchors) or pseudo-score (for graph nodes).
- retrieval_method: "vector", "graph_veta", or "graph_requiere".
- graph_depth: 0 for anchors, 1-2 for expanded nodes.
- boosted_score: Final ranking score after applying method-specific boosts.
';

COMMIT;
