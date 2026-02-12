-- =============================================================================
-- MIGRATION: Collections first-class + server-side collection scoping
-- =============================================================================

BEGIN;

-- 1) Collections catalog
CREATE TABLE IF NOT EXISTS public.collections (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    collection_key text NOT NULL,
    name text,
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'sealed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, collection_key)
);

CREATE INDEX IF NOT EXISTS idx_collections_tenant ON public.collections(tenant_id);

-- 2) First-class collection linkage on core tables
ALTER TABLE public.source_documents
    ADD COLUMN IF NOT EXISTS collection_id uuid REFERENCES public.collections(id);

CREATE INDEX IF NOT EXISTS idx_source_documents_collection ON public.source_documents(collection_id);

ALTER TABLE public.content_chunks
    ADD COLUMN IF NOT EXISTS collection_id uuid;

CREATE INDEX IF NOT EXISTS idx_content_chunks_collection ON public.content_chunks(collection_id);

ALTER TABLE public.regulatory_nodes
    ADD COLUMN IF NOT EXISTS collection_id uuid;

CREATE INDEX IF NOT EXISTS idx_regulatory_nodes_collection ON public.regulatory_nodes(collection_id);

-- 3) Backfill: create collections from legacy metadata on source_documents
WITH legacy_docs AS (
    SELECT
        sd.id,
        sd.institution_id AS tenant_id,
        lower(trim(COALESCE(
            NULLIF(sd.metadata->>'collection_id', ''),
            NULLIF(sd.metadata->>'collection_name', ''),
            'default'
        ))) AS collection_key,
        COALESCE(
            NULLIF(sd.metadata->>'collection_name', ''),
            NULLIF(sd.metadata->>'collection_id', ''),
            'default'
        ) AS collection_name
    FROM public.source_documents sd
    WHERE sd.institution_id IS NOT NULL
)
INSERT INTO public.collections (tenant_id, collection_key, name)
SELECT DISTINCT ld.tenant_id, ld.collection_key, ld.collection_name
FROM legacy_docs ld
ON CONFLICT (tenant_id, collection_key) DO UPDATE
SET name = COALESCE(EXCLUDED.name, public.collections.name);

-- 4) Backfill FKs
WITH resolved AS (
    SELECT
        sd.id AS source_document_id,
        c.id AS collection_id
    FROM public.source_documents sd
    JOIN public.collections c
      ON c.tenant_id = sd.institution_id
     AND c.collection_key = lower(trim(COALESCE(
         NULLIF(sd.metadata->>'collection_id', ''),
         NULLIF(sd.metadata->>'collection_name', ''),
         'default'
     )))
)
UPDATE public.source_documents sd
SET collection_id = r.collection_id
FROM resolved r
WHERE sd.id = r.source_document_id
  AND sd.collection_id IS NULL;

UPDATE public.content_chunks cc
SET collection_id = sd.collection_id
FROM public.source_documents sd
WHERE cc.source_id = sd.id
  AND sd.collection_id IS NOT NULL
  AND cc.collection_id IS NULL;

UPDATE public.regulatory_nodes rn
SET collection_id = sd.collection_id
FROM public.source_documents sd
WHERE rn.source_document_id = sd.id
  AND sd.collection_id IS NOT NULL
  AND rn.collection_id IS NULL;

-- 5) RPC updates: match_knowledge_secure
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
BEGIN
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
            ((filter_conditions->>'tenant_id' IS NULL) OR (cc.institution_id = (filter_conditions->>'tenant_id')::uuid))
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
            AND ((filter_conditions->>'tenant_id' IS NULL) OR (cc.institution_id = (filter_conditions->>'tenant_id')::uuid))
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

-- 6) RPC updates: paginated retrieval
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
BEGIN
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
            ((filter_conditions->>'tenant_id' IS NULL) OR (cc.institution_id = (filter_conditions->>'tenant_id')::uuid))
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
            AND ((filter_conditions->>'tenant_id' IS NULL) OR (cc.institution_id = (filter_conditions->>'tenant_id')::uuid))
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

-- 7) RPC updates: unified retrieval engine
DROP FUNCTION IF EXISTS public.unified_search_context_v2(vector, int, float, float, jsonb, text);

CREATE OR REPLACE FUNCTION public.unified_search_context_v2(
    p_query_embedding vector,
    p_match_count int DEFAULT 20,
    p_match_threshold float DEFAULT 0.25,
    p_hydration_threshold float DEFAULT 0.35,
    p_filter_conditions jsonb DEFAULT '{}'::jsonb,
    p_query_text text DEFAULT NULL
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
        CASE
            WHEN p_query_text IS NOT NULL AND cc.fts @@ plainto_tsquery('spanish', p_query_text)
            THEN ((1 - (cc.embedding <=> p_query_embedding)) * 0.7 + 0.3)::float
            ELSE (1 - (cc.embedding <=> p_query_embedding))::float
        END AS score,
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
      AND ((p_filter_conditions->>'collection_id' IS NULL) OR (cc.collection_id = (p_filter_conditions->>'collection_id')::uuid))
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
                WHEN vector_dims(vn.summary_embedding) = vector_dims(p_query_embedding) THEN
                    CASE
                        WHEN p_query_text IS NOT NULL
                             AND vn.structured_reconstruction::text ILIKE '%' || p_query_text || '%'
                        THEN ((1 - (vn.summary_embedding <=> p_query_embedding)) * 0.6 + 0.4)::float
                        ELSE (1 - (vn.summary_embedding <=> p_query_embedding))::float
                    END
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
      AND ((p_filter_conditions->>'collection_id' IS NULL) OR (pcc.collection_id = (p_filter_conditions->>'collection_id')::uuid))
      AND (
            (
                CASE
                    WHEN vector_dims(vn.summary_embedding) = vector_dims(p_query_embedding)
                        THEN (1 - (vn.summary_embedding <=> p_query_embedding))::float
                    ELSE NULL
                END
            ) >= p_match_threshold
            OR (
                p_query_text IS NOT NULL
                AND vn.structured_reconstruction::text ILIKE '%' || p_query_text || '%'
            )
          )
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

-- 8) RPC updates: summaries now support collection_id
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

GRANT EXECUTE ON FUNCTION public.unified_search_context_v2(vector, int, float, float, jsonb, text) TO authenticated;
GRANT EXECUTE ON FUNCTION public.unified_search_context_v2(vector, int, float, float, jsonb, text) TO service_role;

GRANT EXECUTE ON FUNCTION public.match_summaries(vector(1024), float, int, uuid, uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION public.match_summaries(vector(1024), float, int, uuid, uuid) TO service_role;

COMMIT;
