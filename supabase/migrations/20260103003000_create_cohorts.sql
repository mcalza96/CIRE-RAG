-- Migration: Create Cohorts and Cohort Members for Machine Room Deployment
-- Description: Enables grouping students into cohorts for targeted instrument deployment.

BEGIN;

-- 1. Create cohorts table
CREATE TABLE IF NOT EXISTS public.cohorts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    course_id UUID REFERENCES public.courses(id) ON DELETE SET NULL,
    teacher_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- 2. Create cohort_members table
CREATE TABLE IF NOT EXISTS public.cohort_members (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cohort_id UUID NOT NULL REFERENCES public.cohorts(id) ON DELETE CASCADE,
    student_id UUID NOT NULL REFERENCES public.learners(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT cohort_member_unique UNIQUE (cohort_id, student_id)
);

-- 3. Enable RLS
ALTER TABLE public.cohorts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cohort_members ENABLE ROW LEVEL SECURITY;

-- 4. Policies for cohorts
CREATE POLICY "Teachers can manage their own cohorts"
ON public.cohorts FOR ALL
USING (auth.uid() = teacher_id OR public.is_admin());

-- 5. Policies for cohort_members
CREATE POLICY "Teachers can manage members of their cohorts"
ON public.cohort_members FOR ALL
USING (
    EXISTS (
        SELECT 1 FROM public.cohorts c
        WHERE c.id = cohort_id AND (c.teacher_id = auth.uid() OR public.is_admin())
    )
);

-- 6. Indices for performance
CREATE INDEX IF NOT EXISTS idx_cohorts_teacher ON public.cohorts(teacher_id);
CREATE INDEX IF NOT EXISTS idx_cohort_members_cohort ON public.cohort_members(cohort_id);
CREATE INDEX IF NOT EXISTS idx_cohort_members_student ON public.cohort_members(student_id);

COMMIT;
