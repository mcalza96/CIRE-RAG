-- ============================================================================
-- MIGRATION: Standardize retrieval RPCs
--   1. Drop all dead legacy functions (search_course_knowledge, match_vectors_hybrid, hybrid_search).
--   2. Rewrite match_knowledge_secure & match_knowledge_paginated to use RRF
--      instead of the broken weighted-sum hack (0.7 / 0.3).
--   3. Use untyped `vector` (no hardcoded dimension) so the DB accepts
--      whatever the active embedding model produces.
-- ============================================================================

BEGIN;

-- -----------------------------------------------
-- 1. Drop dead legacy RPCs
-- -----------------------------------------------
DROP FUNCTION IF EXISTS public.search_course_knowledge(text, vector, float, int, uuid);
DROP FUNCTION IF EXISTS public.match_vectors_hybrid(vector, float, int, uuid, uuid, text, text);
-- hybrid_search had two historical signatures (768 and 1024)
DROP FUNCTION IF EXISTS public.hybrid_search(text, vector, float, int, uuid);

-- -----------------------------------------------
-- 2. Rewrite match_knowledge_secure → RRF
-- -----------------------------------------------
CREATE OR REPLACE FUNCTION public.match_knowledge_secure(
    query_embedding vector,
    filter_conditions jsonb,
    match_count int DEFAULT 5,
    match_threshold float DEFAULT 0.5,
    query_text text DEFAULT NULL
)
RETURNS TABLE (
    id uuid,
    content text,
    similarity float,
    metadata jsonb,
    institution_id uuid
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_tenant_id uuid;
    k CONSTANT int := 60; -- RRF constant
BEGIN
    v_tenant_id := NULLIF(filter_conditions->>'tenant_id', '')::uuid;
    IF v_tenant_id IS NULL THEN
        RAISE EXCEPTION 'TENANT_REQUIRED'
            USING ERRCODE = '22023', DETAIL = 'filter_conditions.tenant_id is required';
    END IF;

    RETURN QUERY
    WITH vector_search AS (
        SELECT
            cc.id,
            cc.content,
            (1 - (cc.embedding <=> query_embedding)) AS v_score,
            cc.metadata,
            cc.institution_id,
            ROW_NUMBER() OVER (ORDER BY (cc.embedding <=> query_embedding) ASC) AS rank_vec
        FROM public.content_chunks cc
        WHERE
            cc.institution_id = v_tenant_id
            AND ((filter_conditions->>'is_global' IS NULL) OR (cc.is_global = (filter_conditions->>'is_global')::boolean))
            AND ((filter_conditions->>'source_id' IS NULL) OR (cc.source_id = (filter_conditions->>'source_id')::uuid))
            AND ((filter_conditions->>'collection_id' IS NULL) OR (cc.collection_id = (filter_conditions->>'collection_id')::uuid))
            AND (1 - (cc.embedding <=> query_embedding)) > match_threshold
        ORDER BY cc.embedding <=> query_embedding
        LIMIT match_count * 2
    ),
    text_search AS (
        SELECT
            cc.id,
            ts_rank_cd(cc.fts, plainto_tsquery('spanish', COALESCE(query_text, ''))) AS t_score,
            ROW_NUMBER() OVER (ORDER BY ts_rank_cd(cc.fts, plainto_tsquery('spanish', COALESCE(query_text, ''))) DESC) AS rank_fts
        FROM public.content_chunks cc
        WHERE
            query_text IS NOT NULL
            AND cc.fts @@ plainto_tsquery('spanish', query_text)
            AND cc.institution_id = v_tenant_id
            AND ((filter_conditions->>'is_global' IS NULL) OR (cc.is_global = (filter_conditions->>'is_global')::boolean))
            AND ((filter_conditions->>'source_id' IS NULL) OR (cc.source_id = (filter_conditions->>'source_id')::uuid))
            AND ((filter_conditions->>'collection_id' IS NULL) OR (cc.collection_id = (filter_conditions->>'collection_id')::uuid))
        ORDER BY t_score DESC
        LIMIT match_count * 2
    )
    SELECT
        COALESCE(v.id, (SELECT cc2.id FROM public.content_chunks cc2 WHERE cc2.id = t.id)) AS id,
        COALESCE(v.content, (SELECT cc2.content FROM public.content_chunks cc2 WHERE cc2.id = t.id)) AS content,
        (
            COALESCE(1.0 / (k + v.rank_vec), 0.0) +
            COALESCE(1.0 / (k + t.rank_fts), 0.0)
        )::float AS similarity,
        COALESCE(v.metadata, (SELECT cc2.metadata FROM public.content_chunks cc2 WHERE cc2.id = t.id)) AS metadata,
        COALESCE(v.institution_id, (SELECT cc2.institution_id FROM public.content_chunks cc2 WHERE cc2.id = t.id)) AS institution_id
    FROM vector_search v
    FULL OUTER JOIN text_search t ON v.id = t.id
    ORDER BY similarity DESC
    LIMIT match_count;
END;
$$;

-- -----------------------------------------------
-- 3. Rewrite match_knowledge_paginated → RRF
-- -----------------------------------------------
CREATE OR REPLACE FUNCTION public.match_knowledge_paginated(
    query_embedding vector,
    filter_conditions jsonb,
    match_count int DEFAULT 40,
    match_threshold float DEFAULT 0.5,
    query_text text DEFAULT NULL,
    cursor_score float DEFAULT NULL,
    cursor_id uuid DEFAULT NULL
)
RETURNS TABLE (
    id uuid,
    content text,
    similarity float,
    metadata jsonb,
    institution_id uuid
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_tenant_id uuid;
    k CONSTANT int := 60;
BEGIN
    v_tenant_id := NULLIF(filter_conditions->>'tenant_id', '')::uuid;
    IF v_tenant_id IS NULL THEN
        RAISE EXCEPTION 'TENANT_REQUIRED'
            USING ERRCODE = '22023', DETAIL = 'filter_conditions.tenant_id is required';
    END IF;

    RETURN QUERY
    WITH vector_search AS (
        SELECT
            cc.id,
            cc.content,
            (1 - (cc.embedding <=> query_embedding)) AS v_score,
            cc.metadata,
            cc.institution_id,
            ROW_NUMBER() OVER (ORDER BY (cc.embedding <=> query_embedding) ASC) AS rank_vec
        FROM public.content_chunks cc
        WHERE
            cc.institution_id = v_tenant_id
            AND ((filter_conditions->>'is_global' IS NULL) OR (cc.is_global = (filter_conditions->>'is_global')::boolean))
            AND ((filter_conditions->>'source_id' IS NULL) OR (cc.source_id = (filter_conditions->>'source_id')::uuid))
            AND ((filter_conditions->>'collection_id' IS NULL) OR (cc.collection_id = (filter_conditions->>'collection_id')::uuid))
            AND (1 - (cc.embedding <=> query_embedding)) > match_threshold
        ORDER BY cc.embedding <=> query_embedding
        LIMIT match_count * 2
    ),
    text_search AS (
        SELECT
            cc.id,
            ts_rank_cd(cc.fts, plainto_tsquery('spanish', COALESCE(query_text, ''))) AS t_score,
            ROW_NUMBER() OVER (ORDER BY ts_rank_cd(cc.fts, plainto_tsquery('spanish', COALESCE(query_text, ''))) DESC) AS rank_fts
        FROM public.content_chunks cc
        WHERE
            query_text IS NOT NULL
            AND cc.fts @@ plainto_tsquery('spanish', query_text)
            AND cc.institution_id = v_tenant_id
            AND ((filter_conditions->>'is_global' IS NULL) OR (cc.is_global = (filter_conditions->>'is_global')::boolean))
            AND ((filter_conditions->>'source_id' IS NULL) OR (cc.source_id = (filter_conditions->>'source_id')::uuid))
            AND ((filter_conditions->>'collection_id' IS NULL) OR (cc.collection_id = (filter_conditions->>'collection_id')::uuid))
        ORDER BY t_score DESC
        LIMIT match_count * 2
    ),
    scored AS (
        SELECT
            COALESCE(v.id, (SELECT cc2.id FROM public.content_chunks cc2 WHERE cc2.id = t.id)) AS id,
            COALESCE(v.content, (SELECT cc2.content FROM public.content_chunks cc2 WHERE cc2.id = t.id)) AS content,
            (
                COALESCE(1.0 / (k + v.rank_vec), 0.0) +
                COALESCE(1.0 / (k + t.rank_fts), 0.0)
            )::float AS similarity,
            COALESCE(v.metadata, (SELECT cc2.metadata FROM public.content_chunks cc2 WHERE cc2.id = t.id)) AS metadata,
            COALESCE(v.institution_id, (SELECT cc2.institution_id FROM public.content_chunks cc2 WHERE cc2.id = t.id)) AS institution_id
        FROM vector_search v
        FULL OUTER JOIN text_search t ON v.id = t.id
    )
    SELECT
        s.id,
        s.content,
        s.similarity,
        s.metadata,
        s.institution_id
    FROM scored s
    WHERE
        (
            cursor_score IS NULL
            OR s.similarity < cursor_score
            OR (s.similarity = cursor_score AND s.id > cursor_id)
        )
    ORDER BY s.similarity DESC, s.id ASC
    LIMIT match_count;
END;
$$;

-- -----------------------------------------------
-- 4. match_summaries: keep pure vector search but
--    use untyped vector (no hardcoded dimension)
-- -----------------------------------------------
CREATE OR REPLACE FUNCTION public.match_summaries(
    query_embedding vector,
    match_threshold float DEFAULT 0.5,
    match_count int DEFAULT 10,
    p_tenant_id uuid DEFAULT NULL,
    p_collection_id uuid DEFAULT NULL
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
    IF p_tenant_id IS NULL THEN
        RAISE EXCEPTION 'TENANT_REQUIRED'
            USING ERRCODE = '22023', DETAIL = 'p_tenant_id is required';
    END IF;

    RETURN QUERY
    SELECT
        n.id,
        n.content,
        n.title,
        n.level,
        (1 - (n.embedding <=> query_embedding))::float AS similarity,
        n.properties
    FROM public.regulatory_nodes n
    WHERE n.tenant_id = p_tenant_id
      AND (p_collection_id IS NULL OR n.collection_id = p_collection_id)
      AND n.level > 0
      AND n.embedding IS NOT NULL
      AND (1 - (n.embedding <=> query_embedding)) > match_threshold
    ORDER BY n.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- -----------------------------------------------
-- 5. Re-grant permissions (new signatures use plain `vector`)
-- -----------------------------------------------
GRANT EXECUTE ON FUNCTION public.match_knowledge_secure(vector, jsonb, int, float, text) TO authenticated;
GRANT EXECUTE ON FUNCTION public.match_knowledge_secure(vector, jsonb, int, float, text) TO service_role;
GRANT EXECUTE ON FUNCTION public.match_knowledge_paginated(vector, jsonb, int, float, text, float, uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION public.match_knowledge_paginated(vector, jsonb, int, float, text, float, uuid) TO service_role;
GRANT EXECUTE ON FUNCTION public.match_summaries(vector, float, int, uuid, uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION public.match_summaries(vector, float, int, uuid, uuid) TO service_role;

COMMIT;
