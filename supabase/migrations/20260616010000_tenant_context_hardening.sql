-- Migration: Tenant context hardening for retrieval RPCs
-- Enforces explicit tenant propagation and fail-closed behavior.

BEGIN;

CREATE OR REPLACE FUNCTION public.match_knowledge_secure(
    query_embedding vector(1024),
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
            cc.institution_id
        FROM public.content_chunks cc
        WHERE
            cc.institution_id = v_tenant_id
            AND ((filter_conditions->>'is_global' IS NULL) OR (cc.is_global = (filter_conditions->>'is_global')::boolean))
            AND ((filter_conditions->>'source_id' IS NULL) OR (cc.source_id = (filter_conditions->>'source_id')::uuid))
            AND ((filter_conditions->>'collection_id' IS NULL) OR (cc.collection_id = (filter_conditions->>'collection_id')::uuid))
    ),
    text_search AS (
        SELECT
            cc.id,
            ts_rank_cd(cc.fts, plainto_tsquery('spanish', COALESCE(query_text, ''))) AS t_score
        FROM public.content_chunks cc
        WHERE
            query_text IS NOT NULL
            AND cc.fts @@ plainto_tsquery('spanish', query_text)
            AND cc.institution_id = v_tenant_id
            AND ((filter_conditions->>'is_global' IS NULL) OR (cc.is_global = (filter_conditions->>'is_global')::boolean))
            AND ((filter_conditions->>'source_id' IS NULL) OR (cc.source_id = (filter_conditions->>'source_id')::uuid))
            AND ((filter_conditions->>'collection_id' IS NULL) OR (cc.collection_id = (filter_conditions->>'collection_id')::uuid))
    )
    SELECT
        v.id,
        v.content,
        CASE
            WHEN query_text IS NULL THEN v.v_score
            ELSE (v.v_score * 0.7 + COALESCE(LEAST(t.t_score, 1.0), 0) * 0.3)
        END AS similarity,
        v.metadata,
        v.institution_id
    FROM vector_search v
    LEFT JOIN text_search t ON v.id = t.id
    WHERE
        (
            CASE
                WHEN query_text IS NULL THEN v.v_score
                ELSE (v.v_score * 0.7 + COALESCE(LEAST(t.t_score, 1.0), 0) * 0.3)
            END > match_threshold
        )
        OR (t.t_score > 0.05)
    ORDER BY similarity DESC
    LIMIT match_count;
END;
$$;

CREATE OR REPLACE FUNCTION public.match_knowledge_paginated(
    query_embedding vector(1024),
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
            cc.institution_id
        FROM public.content_chunks cc
        WHERE
            cc.institution_id = v_tenant_id
            AND ((filter_conditions->>'is_global' IS NULL) OR (cc.is_global = (filter_conditions->>'is_global')::boolean))
            AND ((filter_conditions->>'source_id' IS NULL) OR (cc.source_id = (filter_conditions->>'source_id')::uuid))
            AND ((filter_conditions->>'collection_id' IS NULL) OR (cc.collection_id = (filter_conditions->>'collection_id')::uuid))
    ),
    text_search AS (
        SELECT
            cc.id,
            ts_rank_cd(cc.fts, plainto_tsquery('spanish', COALESCE(query_text, ''))) AS t_score
        FROM public.content_chunks cc
        WHERE
            query_text IS NOT NULL
            AND cc.fts @@ plainto_tsquery('spanish', query_text)
            AND cc.institution_id = v_tenant_id
            AND ((filter_conditions->>'is_global' IS NULL) OR (cc.is_global = (filter_conditions->>'is_global')::boolean))
            AND ((filter_conditions->>'source_id' IS NULL) OR (cc.source_id = (filter_conditions->>'source_id')::uuid))
            AND ((filter_conditions->>'collection_id' IS NULL) OR (cc.collection_id = (filter_conditions->>'collection_id')::uuid))
    ),
    scored AS (
        SELECT
            v.id,
            v.content,
            CASE
                WHEN query_text IS NULL THEN v.v_score
                ELSE (v.v_score * 0.7 + COALESCE(LEAST(t.t_score, 1.0), 0) * 0.3)
            END AS similarity,
            v.metadata,
            v.institution_id
        FROM vector_search v
        LEFT JOIN text_search t ON v.id = t.id
        WHERE
            (
                CASE
                    WHEN query_text IS NULL THEN v.v_score
                    ELSE (v.v_score * 0.7 + COALESCE(LEAST(t.t_score, 1.0), 0) * 0.3)
                END > match_threshold
            )
            OR (t.t_score > 0.05)
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

CREATE OR REPLACE FUNCTION public.match_summaries(
    query_embedding vector(1024),
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
        (1 - (n.embedding <=> query_embedding)) AS similarity,
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

ALTER TABLE IF EXISTS public.content_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.content_chunks FORCE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.regulatory_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.regulatory_nodes FORCE ROW LEVEL SECURITY;

GRANT EXECUTE ON FUNCTION public.match_knowledge_secure(vector(1024), jsonb, int, float, text) TO authenticated;
GRANT EXECUTE ON FUNCTION public.match_knowledge_secure(vector(1024), jsonb, int, float, text) TO service_role;
GRANT EXECUTE ON FUNCTION public.match_knowledge_paginated(vector(1024), jsonb, int, float, text, float, uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION public.match_knowledge_paginated(vector(1024), jsonb, int, float, text, float, uuid) TO service_role;
GRANT EXECUTE ON FUNCTION public.match_summaries(vector(1024), float, int, uuid, uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION public.match_summaries(vector(1024), float, int, uuid, uuid) TO service_role;

COMMIT;
