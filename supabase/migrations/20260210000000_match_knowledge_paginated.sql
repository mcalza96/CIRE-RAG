-- Migration: Add paginated knowledge retrieval RPC
-- Description: Cursor-based pagination for iterative retrieval with novelty stopping.
-- Uses (similarity, id) cursor for stable ordering across pages.

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
            -- Tenant/Global Isolation (same as match_knowledge_secure)
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
            OR 
            (t.t_score > 0.05)
    )
    SELECT 
        s.id,
        s.content,
        s.similarity,
        s.metadata,
        s.institution_id
    FROM scored s
    WHERE
        -- Cursor-based pagination: skip rows already seen
        (
            cursor_score IS NULL
            OR s.similarity < cursor_score
            OR (s.similarity = cursor_score AND s.id > cursor_id)
        )
    ORDER BY s.similarity DESC, s.id ASC
    LIMIT match_count;
END;
$$;
