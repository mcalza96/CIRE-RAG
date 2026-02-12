-- Create rubric_nodes table for Structured Rubric RAG
CREATE TABLE IF NOT EXISTS public.rubric_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES public.source_documents(id) ON DELETE CASCADE,
    criterion_title TEXT NOT NULL,
    levels JSONB NOT NULL DEFAULT '{}'::jsonb, -- e.g. { "insatisfactorio": "...", "competente": "..." }
    
    -- Embeddings
    embedding vector(1024), -- Optimized for Jina v3 (or whatever dim efficient model uses, Jina is usually 768 or 1024 depending on config, let's assume 1024 for v3 if that's the model, wait Jina v3 is 1024 usually)
    -- Actually Jina v3 default is 1024.
    
    full_context_embedding vector(1024), -- Alternative embedding of the full JSON for broader context
    
    is_global BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Enable RLS
ALTER TABLE public.rubric_nodes ENABLE ROW LEVEL SECURITY;

-- Policy: Multi-tenant filtered access
DROP POLICY IF EXISTS "Allow authenticated read access" ON public.rubric_nodes;

CREATE POLICY "Allow authenticated read access" ON public.rubric_nodes
    FOR SELECT
    TO authenticated
    USING (
        is_global = true 
        OR 
        (document_id IN (SELECT id FROM public.source_documents WHERE institution_id = (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'institution_id')::uuid))
    );

-- Indexes for Vector Search
-- Note: User needs to create HNSW index if they have many rows. For now just standard table.
CREATE INDEX IF NOT EXISTS idx_rubric_nodes_doc_id ON public.rubric_nodes(document_id);
