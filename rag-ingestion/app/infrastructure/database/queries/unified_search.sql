-- =============================================================================
-- QUERY FUNCTION: unified_search_context
-- Purpose:
--   Hybrid vector retrieval across document text chunks + visual nodes.
--
-- Notes:
-- - `structured_reconstruction` is conditionally returned only when
--   `similarity >= p_hydration_threshold` to reduce large JSON transfer.
-- - Visual node matching uses `visual_summary` embeddings as bait.
-- - Hydration uses markdown/json payload for final LLM context construction.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

DROP FUNCTION IF EXISTS public.unified_search_context(vector, int, float, float, jsonb);

CREATE OR REPLACE FUNCTION public.unified_search_context(
    p_query_embedding vector,
    p_match_count int DEFAULT 20,
    p_match_threshold float DEFAULT 0.25,
    p_hydration_threshold float DEFAULT 0.35,
    p_filter_conditions jsonb DEFAULT '{}'::jsonb
)
RETURNS TABLE (
    id uuid,
    source_type text,
    similarity float,
    score float,
    content text,
    visual_summary text,
    structured_reconstruction jsonb,
    metadata jsonb,
    source_id uuid,
    parent_chunk_id uuid
)
LANGUAGE sql
STABLE
AS $$
WITH text_candidates AS (
    SELECT
        cc.id,
        'document_chunk'::text AS source_type,
        (1 - (cc.embedding <=> p_query_embedding))::float AS similarity,
        (1 - (cc.embedding <=> p_query_embedding))::float AS score,
        cc.content,
        NULL::text AS visual_summary,
        NULL::jsonb AS structured_reconstruction,
        COALESCE(
            jsonb_build_object(
                'page', cc.file_page_number,
                'chunk_index', cc.chunk_index,
                'semantic_context', cc.semantic_context
            ),
            '{}'::jsonb
        ) AS metadata,
        cc.source_id,
        NULL::uuid AS parent_chunk_id
FROM public.content_chunks cc
    WHERE cc.embedding IS NOT NULL
      AND (
            (p_filter_conditions->>'tenant_id' IS NULL)
            OR (cc.institution_id = (p_filter_conditions->>'tenant_id')::uuid)
          )
      AND (
            (p_filter_conditions->>'is_global' IS NULL)
            OR (cc.is_global = (p_filter_conditions->>'is_global')::boolean)
          )
      AND (
            (p_filter_conditions->>'source_id' IS NULL)
            OR (cc.source_id = (p_filter_conditions->>'source_id')::uuid)
          )
      AND (1 - (cc.embedding <=> p_query_embedding)) >= p_match_threshold
),
visual_candidates AS (
    SELECT
        vn.id,
        'visual_node'::text AS source_type,
        (
            CASE
                WHEN vn.summary_embedding IS NULL THEN NULL
                WHEN vector_dims(vn.summary_embedding) = vector_dims(p_query_embedding)
                    THEN (1 - (vn.summary_embedding <=> p_query_embedding))::float
                ELSE NULL
            END
        ) AS similarity,
        (
            CASE
                WHEN vn.summary_embedding IS NULL THEN NULL
                WHEN vector_dims(vn.summary_embedding) = vector_dims(p_query_embedding)
                    THEN (1 - (vn.summary_embedding <=> p_query_embedding))::float
                ELSE NULL
            END
        ) AS score,
        NULL::text AS content,
        vn.visual_summary,
        CASE
            WHEN (
                CASE
                    WHEN vn.summary_embedding IS NULL THEN NULL
                    WHEN vector_dims(vn.summary_embedding) = vector_dims(p_query_embedding)
                        THEN (1 - (vn.summary_embedding <=> p_query_embedding))::float
                    ELSE NULL
                END
            ) >= p_hydration_threshold THEN vn.structured_reconstruction
            ELSE NULL::jsonb
        END AS structured_reconstruction,
        COALESCE(
            (vn.structured_reconstruction -> 'metadata'),
            '{}'::jsonb
        ) AS metadata,
        pcc.source_id,
        vn.parent_chunk_id
    FROM public.visual_nodes vn
    JOIN public.content_chunks pcc ON pcc.id = vn.parent_chunk_id
    WHERE vn.summary_embedding IS NOT NULL
      AND (
            (p_filter_conditions->>'tenant_id' IS NULL)
            OR (pcc.institution_id = (p_filter_conditions->>'tenant_id')::uuid)
          )
      AND (
            (p_filter_conditions->>'is_global' IS NULL)
            OR (pcc.is_global = (p_filter_conditions->>'is_global')::boolean)
          )
      AND (
            (p_filter_conditions->>'source_id' IS NULL)
            OR (pcc.source_id = (p_filter_conditions->>'source_id')::uuid)
          )
      AND (
            CASE
                WHEN vector_dims(vn.summary_embedding) = vector_dims(p_query_embedding)
                    THEN (1 - (vn.summary_embedding <=> p_query_embedding))::float
                ELSE NULL
            END
          ) >= p_match_threshold
),
combined AS (
    SELECT * FROM text_candidates
    UNION ALL
    SELECT * FROM visual_candidates
)
SELECT
    c.id,
    c.source_type,
    c.similarity,
    c.score,
    c.content,
    c.visual_summary,
    c.structured_reconstruction,
    c.metadata,
    c.source_id,
    c.parent_chunk_id
FROM combined c
ORDER BY c.score DESC NULLS LAST
LIMIT GREATEST(p_match_count, 1);
$$;

GRANT EXECUTE ON FUNCTION public.unified_search_context(vector, int, float, float, jsonb) TO authenticated;
GRANT EXECUTE ON FUNCTION public.unified_search_context(vector, int, float, float, jsonb) TO service_role;
