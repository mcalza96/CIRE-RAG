-- =============================================================================
-- MIGRATION: UNIFIED VECTOR SPACE (1024d)
-- Description: Standardizes all vector embeddings to 1024 dimensions (Jina v3).
--              Truncates existing data to ensure consistency.
-- Date: 2026-04-08
-- =============================================================================

BEGIN;

-- 1. TRUNCATE DATA
-- We are changing dimensions, so old vectors are invalid.
TRUNCATE TABLE public.knowledge_chunks CASCADE;
TRUNCATE TABLE public.rubric_nodes CASCADE;
-- Also truncate content_chunks if it is still being used or is an alias
TRUNCATE TABLE public.content_chunks CASCADE;

-- 2. ALTER COLUMNS
-- knowledge_chunks
ALTER TABLE public.knowledge_chunks 
ALTER COLUMN embedding TYPE vector(1024);

-- rubric_nodes
-- Check if embedding column exists, if not add it, if yes alter it
DO $$ 
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'rubric_nodes' AND column_name = 'embedding') THEN
        ALTER TABLE public.rubric_nodes ALTER COLUMN embedding TYPE vector(1024);
    ELSE
        ALTER TABLE public.rubric_nodes ADD COLUMN embedding vector(1024);
    END IF;
END $$;

-- content_chunks (legacy or alias)
ALTER TABLE public.content_chunks 
ALTER COLUMN embedding TYPE vector(1024);


-- 3. OPTIMIZE INDICES (HNSW)
-- Drop old indices
DROP INDEX IF EXISTS idx_knowledge_chunks_embedding;
DROP INDEX IF EXISTS idx_rubric_nodes_embedding;
DROP INDEX IF EXISTS idx_content_chunks_embedding;

-- Create new optimized indices for 1024d
-- m=16, ef_construction=64 are good defaults for 1024d
CREATE INDEX idx_knowledge_chunks_embedding 
ON public.knowledge_chunks USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

CREATE INDEX idx_rubric_nodes_embedding 
ON public.rubric_nodes USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

CREATE INDEX idx_content_chunks_embedding 
ON public.content_chunks USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

COMMIT;
