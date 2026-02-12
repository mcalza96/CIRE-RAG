-- =============================================================================
-- MIGRATION: RLS FINAL SECURITY AUDIT & CONSOLIDATION
-- Description: Enforces strict tenant isolation and global content access
--              on regulatory_nodes and content_chunks.
-- Date: 2026-05-05
-- =============================================================================

BEGIN;

-- 1. REGULATORY NODES ISOLATION
ALTER TABLE public.regulatory_nodes ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "See nodes if member or global" ON public.regulatory_nodes;

-- Policy: A user can see a regulatory node if:
-- 1. It belongs to THEIR tenant (institutional safety).
-- 2. It is marked as GLOBAL (is_global metadata).
-- 3. They are a Global Admin (Staff).
CREATE POLICY "Regulatory isolation policy" ON public.regulatory_nodes
FOR SELECT TO authenticated
USING (
    public.is_admin() OR
    (tenant_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM public.memberships m 
        WHERE m.institution_id = public.regulatory_nodes.tenant_id 
        AND m.user_id = auth.uid()
    )) OR
    ((metadata->>'is_global')::boolean IS TRUE)
);

-- 2. CONTENT CHUNKS ISOLATION
ALTER TABLE public.content_chunks ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Content chunks isolation" ON public.content_chunks;

-- Policy: A user can see a chunk if:
-- 1. It belongs to THEIR institution.
-- 2. They own the document (if personal).
-- 3. They are a Global Admin.
CREATE POLICY "Content chunks isolation policy" ON public.content_chunks
FOR SELECT TO authenticated
USING (
    public.is_admin() OR
    (institution_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM public.memberships m 
        WHERE m.institution_id = public.content_chunks.institution_id 
        AND m.user_id = auth.uid()
    )) OR
    (institution_id IS NULL AND created_by = auth.uid())
);

-- 3. SECURE RPC WRAPPERS
-- Ensure all vector search RPCs are SECURITY DEFINER and use search_path correctly.
-- (Already handled in 20260501_fix_rag_leak.sql for graph guided search).

COMMIT;
