-- Hybrid Retrieval RPC with Reciprocal Rank Fusion (RRF)
-- Combines results from Personal, Tenant, and Global layers.

CREATE OR REPLACE FUNCTION public.match_vectors_hybrid(
    query_embedding vector(1024),
    match_threshold float,
    match_count int,
    p_user_id uuid,
    p_institution_id uuid DEFAULT NULL,
    filter_subject text DEFAULT NULL,
    filter_level text DEFAULT NULL
)
RETURNS TABLE (
    id uuid,
    source_id uuid,
    content text,
    semantic_context text,
    similarity float,
    source_layer text,
    rrf_score float,
    metadata jsonb
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    k int := 60; -- RRF constant
BEGIN
    RETURN QUERY
    WITH personal_hits AS (
        SELECT 
            cc.id,
            cc.source_id,
            cc.content,
            cc.semantic_context,
            1 - (cc.embedding <=> query_embedding) as sim,
            'personal' as layer,
            sd.metadata as sd_metadata
        FROM public.content_chunks cc
        JOIN public.source_documents sd ON cc.source_id = sd.id
        WHERE sd.teacher_id = p_user_id
        AND 1 - (cc.embedding <=> query_embedding) > match_threshold
        ORDER BY cc.embedding <=> query_embedding
        LIMIT match_count * 2
    ),
    tenant_hits AS (
        SELECT 
            cc.id,
            cc.source_id,
            cc.content,
            cc.semantic_context,
            1 - (cc.embedding <=> query_embedding) as sim,
            'tenant' as layer,
            sd.metadata as sd_metadata
        FROM public.content_chunks cc
        JOIN public.source_documents sd ON cc.source_id = sd.id
        WHERE sd.institution_id = p_institution_id
        AND 1 - (cc.embedding <=> query_embedding) > match_threshold
        ORDER BY cc.embedding <=> query_embedding
        LIMIT match_count * 2
    ),
    global_hits AS (
        SELECT 
            cc.id,
            cc.source_id,
            cc.content,
            cc.semantic_context,
            1 - (cc.embedding <=> query_embedding) as sim,
            'global' as layer,
            sd.metadata as sd_metadata
        FROM public.content_chunks cc
        JOIN public.source_documents sd ON cc.source_id = sd.id
        WHERE (sd.metadata->>'is_global')::boolean = true
        AND (filter_subject IS NULL OR sd.metadata->>'subject' = filter_subject)
        AND (filter_level IS NULL OR sd.metadata->>'level' = filter_level)
        AND 1 - (cc.embedding <=> query_embedding) > match_threshold
        ORDER BY cc.embedding <=> query_embedding
        LIMIT match_count * 2
    ),
    all_hits AS (
        SELECT *, row_number() OVER (PARTITION BY layer ORDER BY sim DESC) as rank FROM personal_hits
        UNION ALL
        SELECT *, row_number() OVER (PARTITION BY layer ORDER BY sim DESC) as rank FROM tenant_hits
        UNION ALL
        SELECT *, row_number() OVER (PARTITION BY layer ORDER BY sim DESC) as rank FROM global_hits
    )
    SELECT 
        ah.id,
        ah.source_id,
        ah.content,
        ah.semantic_context,
        MAX(ah.sim) as similarity,
        ah.layer as source_layer,
        SUM(1.0 / (k + ah.rank))::float as rrf_score,
        ah.sd_metadata as metadata
    FROM all_hits ah
    GROUP BY ah.id, ah.source_id, ah.content, ah.semantic_context, ah.layer, ah.sd_metadata
    ORDER BY rrf_score DESC
    LIMIT match_count;
END;
$$;
