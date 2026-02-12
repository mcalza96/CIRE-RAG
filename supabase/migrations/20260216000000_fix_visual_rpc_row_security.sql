-- =============================================================================
-- MIGRATION: Fix Visual RPC row_security + parent lookup robustness
-- Root Cause: SET row_security = off was not persisting in proconfig,
--             and FOUND flag from bare EXECUTE without INTO was unreliable.
-- Changes:
--   1) Recreate function so Postgres stores row_security=off in proconfig.
--   2) Use GET DIAGNOSTICS ROW_COUNT instead of IF NOT FOUND for robustness.
--   3) Default parent table to 'content_chunks' (canonical) instead of
--      dynamic resolution that could pick non-existent tables.
-- =============================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

DROP FUNCTION IF EXISTS public.create_visual_node_transaction(
    uuid, uuid, text, text, text, jsonb, vector, text
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
    v_row_count int;
BEGIN
    -- ── Input guards ────────────────────────────────────────────────────
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

    -- ── Parent table resolution ─────────────────────────────────────────
    -- Default to canonical 'content_chunks'; allow explicit override.
    IF p_parent_chunk_table IS NOT NULL AND btrim(p_parent_chunk_table) <> '' THEN
        v_parent_table := btrim(p_parent_chunk_table);
    ELSE
        v_parent_table := 'content_chunks';
    END IF;

    IF to_regclass(format('public.%s', v_parent_table)) IS NULL THEN
        RAISE EXCEPTION 'Parent table does not exist: %', v_parent_table;
    END IF;

    -- ── Lock parent row ─────────────────────────────────────────────────
    -- FIX: Use GET DIAGNOSTICS instead of IF NOT FOUND for robustness.
    EXECUTE format('SELECT 1 FROM public.%I WHERE id = $1 FOR UPDATE', v_parent_table)
    USING p_parent_chunk_id;

    GET DIAGNOSTICS v_row_count = ROW_COUNT;
    IF v_row_count = 0 THEN
        RAISE EXCEPTION 'Parent chunk id % not found in table % (row_count=0)',
            p_parent_chunk_id, v_parent_table;
    END IF;

    -- ── Insert visual node ──────────────────────────────────────────────
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
        COALESCE(p_structured_reconstruction, '{}'::jsonb),
        p_summary_embedding,
        now(),
        now()
    );

    -- ── Update parent chunk content with anchor token ───────────────────
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

-- Ensure ownership for rolbypassrls
ALTER FUNCTION public.create_visual_node_transaction(
    uuid, uuid, text, text, text, jsonb, vector, text
) OWNER TO postgres;

GRANT EXECUTE ON FUNCTION public.create_visual_node_transaction(
    uuid, uuid, text, text, text, jsonb, vector, text
) TO authenticated;

GRANT EXECUTE ON FUNCTION public.create_visual_node_transaction(
    uuid, uuid, text, text, text, jsonb, vector, text
) TO service_role;

COMMIT;
