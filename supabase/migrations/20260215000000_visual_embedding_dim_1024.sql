-- =============================================================================
-- MIGRATION: Align Visual Embedding Dimension to 1024
-- Rationale:
-- - Retrieval query embeddings in this stack are Jina-based (1024d).
-- - content_chunks.embedding is already vector(1024).
-- - visual_nodes.summary_embedding must match query dimensionality for ANN search.
-- =============================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

-- If a 1536-d vector column exists, convert safely by slicing first 1024 dims.
-- This preserves determinism and unblocks retrieval compatibility.
ALTER TABLE public.visual_nodes
    ALTER COLUMN summary_embedding TYPE vector(1024)
    USING (
        CASE
            WHEN summary_embedding IS NULL THEN NULL
            ELSE subvector(summary_embedding, 1, 1024)::vector(1024)
        END
    );

DROP INDEX IF EXISTS idx_visual_nodes_summary_embedding_hnsw;
CREATE INDEX IF NOT EXISTS idx_visual_nodes_summary_embedding_hnsw
    ON public.visual_nodes
    USING hnsw (summary_embedding vector_cosine_ops)
    WHERE summary_embedding IS NOT NULL;

-- Recreate RPC signature with vector(1024)
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
    p_summary_embedding vector(1024),
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

COMMIT;
