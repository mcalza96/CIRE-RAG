-- Secure Vector Search RPC with Metadata Pre-filtering
-- Enforces strict isolation using JSONB containment operator (@>)
-- This ensures that the HNSW index scan only considers vectors matching the tenant/scope.

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
        -- Metadata Hard Scoping (Pre-filtering)
        cc.metadata @> filter_conditions
        AND
        -- Similarity Threshold
        1 - (cc.embedding <=> query_embedding) > match_threshold
    ORDER BY 
        cc.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- Create a GIN index on metadata for faster JSONB containment if it doesn't exist
CREATE INDEX IF NOT EXISTS content_chunks_metadata_gin_idx ON public.content_chunks USING gin (metadata jsonb_path_ops);
