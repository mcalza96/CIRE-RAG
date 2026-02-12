-- =============================================================================
-- MIGRATION: SCOPED RAG VECTORS (Multi-Tenant Segregation)
-- Description: Adds 'institution_id' and 'is_global' columns to vectors.
--              Implements strict RLS and Scoped Search RPC.
-- Date: 2026-02-06
-- =============================================================================

BEGIN;

-- 1. SCHEMA MODIFICATIONS
-- =============================================================================

-- 1.1 Add Scoping Columns to source_documents
-- We assume source_documents is the parent. We add columns here for data integrity.
DO $$ 
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'source_documents' AND column_name = 'institution_id') THEN
        ALTER TABLE public.source_documents ADD COLUMN institution_id UUID REFERENCES public.institutions(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'source_documents' AND column_name = 'is_global') THEN
        ALTER TABLE public.source_documents ADD COLUMN is_global BOOLEAN DEFAULT false;
    END IF;
END $$;

-- 1.2 Add Scoping Columns to content_chunks (Denormalization for Performance)
-- This allows us to filter vectors directly without joining source_documents during HNSW traversal.
DO $$ 
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'content_chunks' AND column_name = 'institution_id') THEN
        ALTER TABLE public.content_chunks ADD COLUMN institution_id UUID REFERENCES public.institutions(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'content_chunks' AND column_name = 'is_global') THEN
        ALTER TABLE public.content_chunks ADD COLUMN is_global BOOLEAN DEFAULT false;
    END IF;
END $$;

-- 2. INDEXING (The "Chinese Wall" Performance Optimization)
-- =============================================================================

-- 2.1 Partial Index for Global Content
-- Only indexes vectors where is_global = true. Kept small and fast.
CREATE INDEX IF NOT EXISTS idx_content_chunks_embedding_global 
ON public.content_chunks USING hnsw (embedding vector_cosine_ops)
WHERE is_global = true;

-- 2.2 Partial Index for Institutional Content
-- Only indexes vectors belonging to an institution.
CREATE INDEX IF NOT EXISTS idx_content_chunks_embedding_institution 
ON public.content_chunks USING hnsw (embedding vector_cosine_ops)
WHERE institution_id IS NOT NULL;

-- 2.3 Partial Index for Private Content (B2C)
-- Default bucket for personal users.
CREATE INDEX IF NOT EXISTS idx_content_chunks_embedding_private 
ON public.content_chunks USING hnsw (embedding vector_cosine_ops)
WHERE institution_id IS NULL AND is_global = false;


-- 3. SECURITY (RLS Policies)
-- =============================================================================

-- Drop existing policies to ensure clean slate (Optional, but safer for heavy changes)
-- DROP POLICY IF EXISTS "Course owners can manage content_chunks" ON public.content_chunks;

-- 3.1 Policy: GLOBAL ACCESS
-- Any authenticated user can READ global chunks.
CREATE POLICY "Global content is visible to everyone"
ON public.content_chunks FOR SELECT
TO authenticated
USING (is_global = true);

-- 3.2 Policy: INSTITUTIONAL ACCESS
-- User must be a member of the institution to READ.
CREATE POLICY "Institutional content is visible to members"
ON public.content_chunks FOR SELECT
TO authenticated
USING (
    institution_id IS NOT NULL 
    AND EXISTS (
        SELECT 1 FROM public.memberships m
        WHERE m.user_id = auth.uid() 
        AND m.institution_id = public.content_chunks.institution_id
    )
);

-- 3.3 Policy: PRIVATE ACCESS (Personal Workspace)
-- visible to owner only. 
-- Note: 'owner_id' is not on content_chunks in previous schema, but is on 'source_documents'. 
-- We retain the JOIN check for ownership if we didn't add owner_id to chunks.
-- However, for simple filtering we check scopes. 
-- Let's stick to the previous 'Course owners' policy logic for private items, 
-- but explicitly exclude global/institution items from it to avoid conflict.

CREATE POLICY "Private content is visible to course owners"
ON public.content_chunks FOR SELECT
TO authenticated
USING (
    institution_id IS NULL 
    AND is_global = false
    AND EXISTS (
        SELECT 1 FROM public.source_documents sd
        JOIN public.courses c ON c.id = sd.course_id
        WHERE sd.id = public.content_chunks.source_id
        AND (c.teacher_id = auth.uid() OR public.is_admin())
    )
);


-- 4. FUNCTION: Scoped Vector Search
-- =============================================================================

CREATE OR REPLACE FUNCTION public.match_vectors_v2(
    query_embedding vector(1536),
    match_threshold float,
    match_count int,
    search_scope text,
    target_institution_id uuid DEFAULT NULL
)
RETURNS TABLE (
    id uuid,
    content text,
    similarity float,
    source_type text, -- 'global', 'institution', 'private'
    institution_id uuid,
    is_global boolean
)
LANGUAGE plpgsql
AS $$
DECLARE
    -- Security Check Variable
    has_access boolean;
BEGIN
    -- 1. Security Validation for Institutional Scope
    IF (search_scope = 'institution' OR search_scope = 'hybrid_institution') THEN
        IF target_institution_id IS NULL THEN
            RAISE EXCEPTION 'target_institution_id is required for institutional search';
        END IF;

        -- Verify membership (Double check, even if RLS exists, good for RPC logic flow)
        SELECT EXISTS (
            SELECT 1 FROM public.memberships 
            WHERE user_id = auth.uid() 
            AND institution_id = target_institution_id
        ) INTO has_access;

        IF NOT has_access AND NOT public.is_admin() THEN
             RAISE EXCEPTION 'Access Denied: Not a member of the target institution';
        END IF;
    END IF;

    -- 2. Execute Query based on Scope
    RETURN QUERY
    SELECT 
        cc.id,
        cc.content,
        (1 - (cc.embedding <=> query_embedding)) as similarity,
        CASE 
            WHEN cc.is_global THEN 'global'
            WHEN cc.institution_id IS NOT NULL THEN 'institution'
            ELSE 'private'
        END as source_type,
        cc.institution_id,
        cc.is_global
    FROM public.content_chunks cc
    WHERE 1 - (cc.embedding <=> query_embedding) > match_threshold
    AND (
        -- Scope Logic
        CASE 
            WHEN search_scope = 'global' THEN 
                cc.is_global = true
            
            WHEN search_scope = 'institution' THEN 
                cc.institution_id = target_institution_id
            
            WHEN search_scope = 'private' THEN 
                cc.institution_id IS NULL AND cc.is_global = false
            
            WHEN search_scope = 'hybrid_institution' THEN 
                cc.is_global = true OR cc.institution_id = target_institution_id
            
            WHEN search_scope = 'hybrid_private' THEN 
                cc.is_global = true OR (cc.institution_id IS NULL AND cc.is_global = false)
            
            ELSE false -- Safe default
        END
    )
    ORDER BY similarity DESC
    LIMIT match_count;

END;
$$ SECURITY DEFINER SET search_path = public, extensions;

GRANT EXECUTE ON FUNCTION public.match_vectors_v2(vector, float, int, text, uuid) TO authenticated;

COMMIT;
