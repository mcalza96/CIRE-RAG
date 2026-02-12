
-- Migration: Hybrid Search Remediation
-- Description: Adds FTS column and Spanish GIN index for BM25 support.
-- Updates match_knowledge_secure RPC to linear interpolation (0.7 Vector / 0.3 Keyword).

-- 1. Ensure Index exists for full text search (column fts is GENERATED ALWAYS and already exists)
CREATE INDEX IF NOT EXISTS content_chunks_fts_idx ON public.content_chunks USING gin(fts);

-- 2. Update match_knowledge_secure to support Hybrid Search
-- Using DEFAULT NULL to avoid breaking existing Python callers before they are updated.
CREATE OR REPLACE FUNCTION public.match_knowledge_secure(
    query_embedding vector(1024),
    filter_conditions jsonb,
    match_count int DEFAULT 5,
    match_threshold float DEFAULT 0.5,
    query_text text DEFAULT NULL -- New optional parameter
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
            -- Tenant/Global Isolation
            (
                (filter_conditions->>'tenant_id' IS NULL) OR 
                (cc.institution_id = (filter_conditions->>'tenant_id')::uuid)
            )
            AND
            (
                (filter_conditions->>'is_global' IS NULL) OR 
                (cc.is_global = (filter_conditions->>'is_global')::boolean)
            )
            AND
            (
                (filter_conditions->>'source_id' IS NULL) OR 
                (cc.source_id = (filter_conditions->>'source_id')::uuid)
            )
    ),
    text_search AS (
        SELECT 
            cc.id,
            ts_rank_cd(cc.fts, plainto_tsquery('spanish', COALESCE(query_text, ''))) AS t_score
        FROM public.content_chunks cc
        WHERE 
            query_text IS NOT NULL AND cc.fts @@ plainto_tsquery('spanish', query_text)
            AND
            (
                (filter_conditions->>'tenant_id' IS NULL) OR 
                (cc.institution_id = (filter_conditions->>'tenant_id')::uuid)
            )
            AND
            (
                (filter_conditions->>'is_global' IS NULL) OR 
                (cc.is_global = (filter_conditions->>'is_global')::boolean)
            )
            AND
            (
                (filter_conditions->>'source_id' IS NULL) OR 
                (cc.source_id = (filter_conditions->>'source_id')::uuid)
            )
    )
    SELECT 
        v.id,
        v.content,
        -- Score Fusion: If keyword matches, boost the vector score.
        -- Balanced weights: Vector (70%) + Keyword (30%)
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
        OR 
        -- Fallback: Surface strong keyword hits (e.g. Exercise 33) even if vector embedding is distant
        (t.t_score > 0.05)
    ORDER BY similarity DESC
    LIMIT match_count;
END;
$$;
