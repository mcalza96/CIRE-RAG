-- RUNBOOK DE ROLLOUT: PHASE 4 - INDICES DB
-- Performance indices for latency reduction (HNSW + Filtering)

-- Note: 'IF NOT EXISTS' handles idenpotency, but we use CREATE INDEX CONCURRENTLY which cannot run in a transaction block.
-- Supabase migrations usually run in transactions. If this fails, it might need to be run manually or without transaction.
-- For safety in automated migration, we omit CONCURRENTLY here if it's running inside a transaction, 
-- BUT for production on large tables, CONCURRENTLY is critical.
-- Assuming this script might be run manually or via a tool that handles it.
-- We will use standard CREATE INDEX
-- 1. Optimizar índice HNSW para búsqueda semántica
DROP INDEX IF EXISTS public.idx_content_chunks_embedding;
CREATE INDEX idx_content_chunks_embedding 
ON public.content_chunks 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- 2. Índice para aislamiento de tenant (multi-tenancy) y filtrado rápido
-- Nota: Se usa 'institution_id' que es el nombre real en el esquema.
-- Se añade indexación para búsqueda global y por colección.
CREATE INDEX IF NOT EXISTS idx_content_chunks_isolation
ON public.content_chunks (institution_id, is_global, collection_id);

-- 3. Índice para filtrado por metadatos de estándar (si existe en el JSONB)
CREATE INDEX IF NOT EXISTS idx_content_chunks_source_standard
ON public.content_chunks ((metadata->>'source_standard'), institution_id);

-- 3. Configurar ef_search para balance recall/latencia (Session level, but good to document)
-- This applies to the current session, so it might need to be set in the application connection or postgres config.
-- SET hnsw.ef_search = 80;
