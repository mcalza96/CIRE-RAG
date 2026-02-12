-- =============================================================================
-- MIGRATION: COURSE ASSIGNMENTS (B2B Shared Visibility)
-- Description: Enables assigning multiple teachers to an institutional course.
-- Date: 2026-02-08
-- =============================================================================

BEGIN;

-- 1. TABLE: Course Assignments
-- Links courses to teachers within an institutional context.
CREATE TABLE IF NOT EXISTS public.course_assignments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id UUID NOT NULL REFERENCES public.courses(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('lead', 'assistant')) DEFAULT 'lead',
    created_at TIMESTAMPTZ DEFAULT now(),
    
    -- Ensure a teacher is assigned only once to a course
    CONSTRAINT uq_course_assignment_user UNIQUE (course_id, user_id)
);

-- Index for lookup performance
CREATE INDEX IF NOT EXISTS idx_course_assignments_user ON public.course_assignments(user_id);
CREATE INDEX IF NOT EXISTS idx_course_assignments_course ON public.course_assignments(course_id);

-- 2. SECURITY (RLS)
ALTER TABLE public.course_assignments ENABLE ROW LEVEL SECURITY;

-- 2.1 Policy: Visibility
-- Users can see assignments for courses they are part of, or if they are institution admins.
CREATE POLICY "See course assignments if member or institution admin"
ON public.course_assignments
FOR SELECT
TO authenticated
USING (
    user_id = auth.uid() OR
    public.is_admin() OR
    EXISTS (
        SELECT 1 FROM public.courses c
        JOIN public.memberships m ON m.institution_id = c.institution_id
        WHERE c.id = public.course_assignments.course_id
        AND m.user_id = auth.uid()
        AND m.role = 'admin'
    )
);

-- 2.2 Policy: Management (Institution Admins only for Institutional Courses)
CREATE POLICY "Institution Admins manage course assignments"
ON public.course_assignments
FOR ALL
TO authenticated
USING (
    public.is_admin() OR
    EXISTS (
        SELECT 1 FROM public.courses c
        JOIN public.memberships m ON m.institution_id = c.institution_id
        WHERE c.id = public.course_assignments.course_id
        AND m.user_id = auth.uid()
        AND m.role = 'admin'
    )
)
WITH CHECK (
    public.is_admin() OR
    EXISTS (
        SELECT 1 FROM public.courses c
        JOIN public.memberships m ON m.institution_id = c.institution_id
        WHERE c.id = public.course_assignments.course_id
        AND m.user_id = auth.uid()
        AND m.role = 'admin'
    )
);

COMMIT;
