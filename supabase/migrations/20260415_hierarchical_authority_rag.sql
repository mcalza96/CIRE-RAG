-- =============================================================================
-- MIGRATION: HIERARCHICAL KNOWLEDGE AUTHORITY (Three-Layer Model)
-- Description: Establishes the Three Layers of Authority:
--   Layer 0: Global Library Assets (Public/SuperAdmin)
--   Layer 1: Institutional Mandates (Tenant Admin, Hard Constraints)
--   Layer 2: Course Knowledge Mounts (Instructor Subscription)
-- Date: 2026-04-15
-- =============================================================================

BEGIN;

-- 1. ENUMS
-- =============================================================================
DO $$ 
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'enforcement_level_enum') THEN
        CREATE TYPE public.enforcement_level_enum AS ENUM ('advisory', 'hard_constraint');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'knowledge_source_type_enum') THEN
        CREATE TYPE public.knowledge_source_type_enum AS ENUM ('global', 'mandate');
    END IF;
END $$;

-- 2. TABLES
-- =============================================================================

-- Layer 0: Global Library Assets
-- This evolves the existing global_assets (if any) or creates the new authoritative catalog.
CREATE TABLE IF NOT EXISTS public.global_library_assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    author TEXT,
    summary TEXT,
    tags TEXT[] DEFAULT ARRAY[]::TEXT[],
    
    -- ID linking to where the embeddings live (Vector Store)
    vector_collection_id TEXT NOT NULL,
    
    metadata JSONB DEFAULT '{}'::jsonb,
    
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Layer 1: Institutional Mandates
-- These are the "Rules of the Land" for a specific institution.
CREATE TABLE IF NOT EXISTS public.institutional_mandates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    institution_id UUID NOT NULL REFERENCES public.institutions(id) ON DELETE CASCADE,
    
    title TEXT NOT NULL,
    description TEXT,
    
    -- Critical field for RAG behavior
    enforcement_level public.enforcement_level_enum DEFAULT 'hard_constraint' NOT NULL,
    
    -- Link to the vector store
    vector_ref TEXT NOT NULL,
    
    metadata JSONB DEFAULT '{}'::jsonb,
    
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Layer 2: Course Knowledge Mounts (Symbolic Links)
-- Bridges courses with global assets via explicit subscription.
CREATE TABLE IF NOT EXISTS public.course_knowledge_mounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id UUID NOT NULL REFERENCES public.courses(id) ON DELETE CASCADE,
    global_library_asset_id UUID NOT NULL REFERENCES public.global_library_assets(id) ON DELETE CASCADE,
    
    -- Allow overriding behavior at course level? (e.g., custom weights)
    settings JSONB DEFAULT '{}'::jsonb,
    
    created_at TIMESTAMPTZ DEFAULT now(),
    
    -- Constraint: Prevent duplicate mounts
    CONSTRAINT uq_course_asset_mount UNIQUE (course_id, global_library_asset_id)
);

-- 3. INDEXES
-- =============================================================================
CREATE INDEX IF NOT EXISTS idx_global_lib_tags ON public.global_library_assets USING gin(tags);
CREATE INDEX IF NOT EXISTS idx_inst_mandates_institution ON public.institutional_mandates(institution_id);
CREATE INDEX IF NOT EXISTS idx_course_mounts_course ON public.course_knowledge_mounts(course_id);
CREATE INDEX IF NOT EXISTS idx_course_mounts_asset ON public.course_knowledge_mounts(global_library_asset_id);

-- 4. SECURITY (RLS)
-- =============================================================================
ALTER TABLE public.global_library_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.institutional_mandates ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.course_knowledge_mounts ENABLE ROW LEVEL SECURITY;

-- 4.1 Global Library Assets: Readable by all, editable by SuperAdmin only
CREATE POLICY "Global library is readable by all authenticated"
ON public.global_library_assets FOR SELECT TO authenticated USING (TRUE);

CREATE POLICY "SuperAdmin manages global library"
ON public.global_library_assets FOR ALL TO authenticated
USING (public.is_admin()) WITH CHECK (public.is_admin());

-- 4.2 Institutional Mandates: Readable by members, editable by Institution Admins
CREATE POLICY "Members can read their institution mandates"
ON public.institutional_mandates FOR SELECT TO authenticated
USING (
    EXISTS (
        SELECT 1 FROM public.memberships m
        WHERE m.institution_id = public.institutional_mandates.institution_id
        AND m.user_id = auth.uid()
    )
    OR public.is_admin()
);

CREATE POLICY "Institution Admins manage mandates"
ON public.institutional_mandates FOR ALL TO authenticated
USING (
    EXISTS (
        SELECT 1 FROM public.memberships m
        WHERE m.institution_id = public.institutional_mandates.institution_id
        AND m.user_id = auth.uid()
        AND m.role = 'admin'
    )
    OR public.is_admin()
);

-- 4.3 Course Knowledge Mounts: Managed by Course Teacher
CREATE POLICY "Teachers manage mounts for their courses"
ON public.course_knowledge_mounts FOR ALL TO authenticated
USING (
    EXISTS (
        SELECT 1 FROM public.courses c
        WHERE c.id = public.course_knowledge_mounts.course_id
        AND (c.teacher_id = auth.uid() OR public.is_admin())
    )
)
WITH CHECK (
    EXISTS (
        SELECT 1 FROM public.courses c
        WHERE c.id = public.course_knowledge_mounts.course_id
        AND (c.teacher_id = auth.uid() OR public.is_admin())
    )
);

-- 5. LOGIC: Heritage Function (RPC)
-- =============================================================================
CREATE OR REPLACE FUNCTION public.get_course_context_map(p_course_uuid UUID)
RETURNS TABLE (
    resource_id UUID,
    source_type TEXT,
    enforcement_level TEXT,
    vector_ref TEXT,
    title TEXT
) AS $$
DECLARE
    v_institution_id UUID;
BEGIN
    -- 1. Identify the institution of the course
    SELECT institution_id INTO v_institution_id
    FROM public.courses
    WHERE id = p_course_uuid;

    RETURN QUERY
    -- Layer 1: Institutional Mandates (Automatic Inheritance)
    SELECT 
        im.id as resource_id,
        'mandate'::TEXT as source_type,
        im.enforcement_level::TEXT as enforcement_level,
        im.vector_ref,
        im.title
    FROM public.institutional_mandates im
    WHERE im.institution_id = v_institution_id

    UNION ALL

    -- Layer 2: Course Knowledge Mounts (Explicit Subscriptions to Layer 0)
    SELECT 
        ga.id as resource_id,
        'global'::TEXT as source_type,
        'advisory'::TEXT as enforcement_level, -- Assets globals are usually advisory by default
        ga.vector_collection_id as vector_ref,
        ga.title
    FROM public.course_knowledge_mounts ckm
    JOIN public.global_library_assets ga ON ga.id = ckm.global_library_asset_id
    WHERE ckm.course_id = p_course_uuid;
END;
$$ LANGUAGE plpgsql STABLE SECURITY DEFINER;

COMMIT;
