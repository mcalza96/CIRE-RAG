-- =============================================================================
-- MIGRATION: DYNAMIC CURRICULAR MOUNTING (Phase 4)
-- Description: Refactors courses to use Taxonomy FKs and implements
--              the "Smart Intersection" trigger for auto-mounting content.
-- Date: 2026-04-04
-- =============================================================================

BEGIN;

-- 1. REFACTOR COURSES TABLE
-- Replace text columns with Taxonomy FKs
-- We use ALTER TABLE to add columns. We keep old text columns for safety/migration if needed, or drop them?
-- User instructions say "Reemplaza (o migra)". We will add new ones.
ALTER TABLE public.courses
    ADD COLUMN IF NOT EXISTS taxonomy_context_id UUID REFERENCES public.taxonomies(id),
    ADD COLUMN IF NOT EXISTS taxonomy_level_id UUID REFERENCES public.taxonomies(id),
    ADD COLUMN IF NOT EXISTS taxonomy_subject_id UUID REFERENCES public.taxonomies(id);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_courses_taxonomy_level ON public.courses(taxonomy_level_id);
CREATE INDEX IF NOT EXISTS idx_courses_taxonomy_subject ON public.courses(taxonomy_subject_id);

-- 2. CREATE COURSE_CONTENT_MOUNTS
-- This table links a course to specific source_documents (Intersection)
CREATE TABLE IF NOT EXISTS public.course_content_mounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id UUID NOT NULL REFERENCES public.courses(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES public.source_documents(id) ON DELETE CASCADE,
    mount_type TEXT CHECK (mount_type IN ('automatic', 'manual_supplement', 'transversal')) DEFAULT 'automatic',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    -- Deduplication: A doc can only be mounted once per course
    UNIQUE(course_id, document_id)
);

-- RLS: Teachers can see mounts for their own courses
ALTER TABLE public.course_content_mounts ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Teachers can view mounts for their courses" ON public.course_content_mounts;
CREATE POLICY "Teachers can view mounts for their courses"
    ON public.course_content_mounts
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.courses c
            WHERE c.id = public.course_content_mounts.course_id
            AND c.teacher_id = auth.uid()
        )
    );

-- 3. SMART INTERSECTION TRIGGER FUNCTION
CREATE OR REPLACE FUNCTION public.fn_auto_mount_content()
RETURNS TRIGGER AS $$
DECLARE
    rubric_root_id UUID;
    transversal_node_id UUID;
BEGIN
    -- Only proceed if we have at least context and level
    IF NEW.taxonomy_level_id IS NULL THEN
        RETURN NEW;
    END IF;

    -- A. EXACT MATCH (Content)
    -- Document must have taxonomy_level_id AND taxonomy_subject_id
    IF NEW.taxonomy_subject_id IS NOT NULL THEN
        INSERT INTO public.course_content_mounts (course_id, document_id, mount_type)
        SELECT 
            NEW.id,
            dt_level.document_id,
            'automatic'
        FROM public.document_taxonomy dt_level
        JOIN public.document_taxonomy dt_subject ON dt_level.document_id = dt_subject.document_id
        WHERE 
            dt_level.node_id = NEW.taxonomy_level_id
            AND dt_subject.node_id = NEW.taxonomy_subject_id
        ON CONFLICT (course_id, document_id) DO NOTHING;
    END IF;

    -- B. RUBRIC MATCH (Level Only + Type Rubric)
    -- We identify rubrics by checking if they are linked to the 'Rubric' Root Node
    -- or any node that descends from it? 
    -- Simplest Phase 1/2 logic: Metadata typeId pointed to Rubric Root.
    -- So document_taxonomy should contain the Rubric Root ID.
    
    SELECT id INTO rubric_root_id FROM public.taxonomies WHERE slug = 'rubric' AND type = 'root' LIMIT 1;
    
    IF rubric_root_id IS NOT NULL THEN
        INSERT INTO public.course_content_mounts (course_id, document_id, mount_type)
        SELECT 
            NEW.id,
            dt_level.document_id,
            'automatic'
        FROM public.document_taxonomy dt_level
        JOIN public.document_taxonomy dt_type ON dt_level.document_id = dt_type.document_id
        WHERE 
            dt_level.node_id = NEW.taxonomy_level_id
            AND dt_type.node_id = rubric_root_id
        ON CONFLICT (course_id, document_id) DO NOTHING;
    END IF;

    -- C. TRANSVERSAL / WILDCARD
    -- Documents linked to a specific 'transversal' node (slug='transversal' or 'all-levels')
    -- OR documents that don't have a level but are in the Context? (Maybe too broad)
    -- Let's stick to explicit 'transversal' node.
    
    SELECT id INTO transversal_node_id FROM public.taxonomies WHERE slug = 'transversal' LIMIT 1;
    
    IF transversal_node_id IS NOT NULL THEN
         INSERT INTO public.course_content_mounts (course_id, document_id, mount_type)
         SELECT 
            NEW.id,
            dt.document_id,
            'transversal'
         FROM public.document_taxonomy dt
         WHERE dt.node_id = transversal_node_id
         ON CONFLICT (course_id, document_id) DO NOTHING;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 4. APPLY TRIGGER
DROP TRIGGER IF EXISTS trg_courses_auto_mount ON public.courses;
CREATE TRIGGER trg_courses_auto_mount
    AFTER INSERT ON public.courses
    FOR EACH ROW
    EXECUTE FUNCTION public.fn_auto_mount_content();

-- 5. UPDATE DOCUMENT RLS POLICIES
-- Allow access if mounted
DROP POLICY IF EXISTS "Access to Global or Mounted Documents" ON public.source_documents;

CREATE POLICY "Access to Global or Mounted Documents" ON public.source_documents
    FOR SELECT
    TO authenticated
    USING (
        -- 1. Open Access (Global/Public)
        (metadata->>'is_public')::boolean = true 
        OR 
        (metadata->>'is_global')::boolean = true
        OR
        -- 2. Mounted to my course
        EXISTS (
            SELECT 1 FROM public.course_content_mounts ccm
            JOIN public.courses c ON ccm.course_id = c.id
            WHERE 
                ccm.document_id = public.source_documents.id
                AND c.teacher_id = auth.uid()
        )
        OR
        -- 3. I am the owner (if using teacher_id locally, though source_documents might be admin owned)
        -- Assuming a generic owner check if column exists, for now rely on admin/service_role having bypass
        (
             -- Fallback for direct ownership if course_id link exists directly (Start of Phase 1 style)
             -- We keep this for backward compat if needed
             EXISTS (
                SELECT 1 FROM public.courses c
                WHERE c.id = public.source_documents.course_id
                AND c.teacher_id = auth.uid()
             )
        )
    );

COMMIT;
