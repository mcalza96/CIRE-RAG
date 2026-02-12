-- Migration: Fix match_knowledge_secure RPC for correct column filtering
-- Date: 2026-02-07

CREATE OR REPLACE FUNCTION public.match_knowledge_secure(
    query_embedding vector(1024),
    filter_conditions jsonb,
    match_count int DEFAULT 5,
    match_threshold float DEFAULT 0.5
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
    SELECT 
        cc.id,
        cc.content,
        1 - (cc.embedding <=> query_embedding) AS similarity,
        cc.metadata,
        cc.institution_id
    FROM public.content_chunks cc
    WHERE 
        -- 1. Tenant Isolation (Column-based)
        (
            (filter_conditions->>'tenant_id' IS NULL) OR 
            (cc.institution_id = (filter_conditions->>'tenant_id')::uuid)
        )
        AND
        -- 2. Global Content (Column-based)
        (
            (filter_conditions->>'is_global' IS NULL) OR 
            (cc.is_global = (filter_conditions->>'is_global')::boolean)
        )
        AND
        -- 3. Source Filtering (Column-based)
        (
            (filter_conditions->>'source_id' IS NULL) OR 
            (cc.source_id = (filter_conditions->>'source_id')::uuid)
        )
        AND
        -- 4. Similarity Threshold
        1 - (cc.embedding <=> query_embedding) > match_threshold
    ORDER BY 
        cc.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;
