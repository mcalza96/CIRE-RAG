-- =============================================================================
-- MIGRATION: Visual Anchor Hotfixes
-- Description:
-- 1) Ensure cache table exists in Supabase migrations path.
-- 2) Create/replace create_visual_node_transaction RPC.
-- 3) Create/replace unified_search_context RPC with tenant/global filters.
-- =============================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;

-- -----------------------------------------------------------------------------
-- 1) Visual extraction cache table
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.cache_visual_extractions (
    image_hash VARCHAR(64) NOT NULL,
    provider VARCHAR(64) NOT NULL,
    model_version VARCHAR(128) NOT NULL,
    result_data JSONB NOT NULL,
    token_usage JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT cache_visual_extractions_pk PRIMARY KEY (image_hash, provider, model_version)
);

CREATE INDEX IF NOT EXISTS idx_cache_visual_extractions_created_at
    ON public.cache_visual_extractions (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_cache_visual_extractions_token_usage_gin
    ON public.cache_visual_extractions USING gin (token_usage)
    WHERE token_usage IS NOT NULL;

-- -----------------------------------------------------------------------------
-- 2) Atomic visual-node stitching RPC
-- -----------------------------------------------------------------------------
DROP FUNCTION IF EXISTS public.create_visual_node_transaction(
    uuid,
    uuid,
    text,
    text,
    text,
    jsonb,
    vector,
    text
);

CREATE OR REPLACE FUNCTION public.create_visual_node_transaction(
    p_visual_node_id uuid,
    p_parent_chunk_id uuid,
    p_parent_chunk_text_with_anchor text,
    p_image_storage_path text,
    p_visual_summary text,
    p_structured_reconstruction jsonb,
    p_summary_embedding vector(1536),
    p_parent_chunk_table text DEFAULT NULL
)
RETURNS TABLE (
    visual_node_id uuid,
    parent_table text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
SET row_security = off
AS $$
DECLARE
    v_parent_table text;
    v_has_updated_at boolean;
    v_structured_reconstruction jsonb;
BEGIN
    IF p_visual_node_id IS NULL THEN
        RAISE EXCEPTION 'p_visual_node_id cannot be null';
    END IF;
    IF p_parent_chunk_id IS NULL THEN
        RAISE EXCEPTION 'p_parent_chunk_id cannot be null';
    END IF;
    IF p_parent_chunk_text_with_anchor IS NULL THEN
        RAISE EXCEPTION 'p_parent_chunk_text_with_anchor cannot be null';
    END IF;
    IF p_image_storage_path IS NULL OR btrim(p_image_storage_path) = '' THEN
        RAISE EXCEPTION 'p_image_storage_path cannot be empty';
    END IF;
    IF p_visual_summary IS NULL OR btrim(p_visual_summary) = '' THEN
        RAISE EXCEPTION 'p_visual_summary cannot be empty';
    END IF;

    v_structured_reconstruction := COALESCE(p_structured_reconstruction, '{}'::jsonb);

    IF p_parent_chunk_table IS NOT NULL AND btrim(p_parent_chunk_table) <> '' THEN
        v_parent_table := btrim(p_parent_chunk_table);
    ELSE
        IF to_regclass('public.document_chunks') IS NOT NULL THEN
            v_parent_table := 'document_chunks';
        ELSIF to_regclass('public.content_chunks') IS NOT NULL THEN
            v_parent_table := 'content_chunks';
        ELSIF to_regclass('public.knowledge_chunks') IS NOT NULL THEN
            v_parent_table := 'knowledge_chunks';
        ELSIF to_regclass('public.site_pages_sections') IS NOT NULL THEN
            v_parent_table := 'site_pages_sections';
        ELSE
            RAISE EXCEPTION 'No supported parent chunk table found';
        END IF;
    END IF;

    EXECUTE format('SELECT 1 FROM public.%I WHERE id = $1 FOR UPDATE', v_parent_table)
    USING p_parent_chunk_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Parent chunk id % not found in table %', p_parent_chunk_id, v_parent_table;
    END IF;

    INSERT INTO public.visual_nodes (
        id,
        parent_chunk_id,
        image_storage_path,
        visual_summary,
        structured_reconstruction,
        summary_embedding,
        created_at,
        updated_at
    )
    VALUES (
        p_visual_node_id,
        p_parent_chunk_id,
        p_image_storage_path,
        p_visual_summary,
        v_structured_reconstruction,
        p_summary_embedding,
        now(),
        now()
    );

    SELECT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = v_parent_table
          AND column_name = 'updated_at'
    ) INTO v_has_updated_at;

    IF v_has_updated_at THEN
        EXECUTE format(
            'UPDATE public.%I SET content = $1, updated_at = now() WHERE id = $2',
            v_parent_table
        ) USING p_parent_chunk_text_with_anchor, p_parent_chunk_id;
    ELSE
        EXECUTE format(
            'UPDATE public.%I SET content = $1 WHERE id = $2',
            v_parent_table
        ) USING p_parent_chunk_text_with_anchor, p_parent_chunk_id;
    END IF;

    RETURN QUERY SELECT p_visual_node_id, v_parent_table;
END;
$$;

GRANT EXECUTE ON FUNCTION public.create_visual_node_transaction(
    uuid, uuid, text, text, text, jsonb, vector, text
) TO authenticated;
GRANT EXECUTE ON FUNCTION public.create_visual_node_transaction(
    uuid, uuid, text, text, text, jsonb, vector, text
) TO service_role;

-- -----------------------------------------------------------------------------
-- 3) Unified search with hydration-aware payload transfer
-- -----------------------------------------------------------------------------
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
      AND ((p_filter_conditions->>'tenant_id' IS NULL) OR (cc.institution_id = (p_filter_conditions->>'tenant_id')::uuid))
      AND ((p_filter_conditions->>'is_global' IS NULL) OR (cc.is_global = (p_filter_conditions->>'is_global')::boolean))
      AND ((p_filter_conditions->>'source_id' IS NULL) OR (cc.source_id = (p_filter_conditions->>'source_id')::uuid))
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
        COALESCE((vn.structured_reconstruction -> 'metadata'), '{}'::jsonb) AS metadata,
        pcc.source_id,
        vn.parent_chunk_id
    FROM public.visual_nodes vn
    JOIN public.content_chunks pcc ON pcc.id = vn.parent_chunk_id
    WHERE vn.summary_embedding IS NOT NULL
      AND ((p_filter_conditions->>'tenant_id' IS NULL) OR (pcc.institution_id = (p_filter_conditions->>'tenant_id')::uuid))
      AND ((p_filter_conditions->>'is_global' IS NULL) OR (pcc.is_global = (p_filter_conditions->>'is_global')::boolean))
      AND ((p_filter_conditions->>'source_id' IS NULL) OR (pcc.source_id = (p_filter_conditions->>'source_id')::uuid))
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

COMMIT;
