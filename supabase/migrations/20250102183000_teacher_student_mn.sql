-- Migration: 1:N to M:N Teacher-Student Relationship (FIXED DEPENDENCIES)

BEGIN;

-- 1. Update app_role enum
ALTER TYPE public.app_role ADD VALUE IF NOT EXISTS 'student';

-- 2. Create teacher_student_mapping table
CREATE TABLE IF NOT EXISTS public.teacher_student_mapping (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    student_id UUID NOT NULL REFERENCES public.learners(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT teacher_student_mapping_unique UNIQUE (teacher_id, student_id)
);

-- 3. Enable RLS on mapping table immediately
ALTER TABLE public.teacher_student_mapping ENABLE ROW LEVEL SECURITY;

-- 4. Migrate existing data
INSERT INTO public.teacher_student_mapping (teacher_id, student_id)
SELECT teacher_id, id
FROM public.learners
WHERE teacher_id IS NOT NULL
ON CONFLICT (teacher_id, student_id) DO NOTHING;

-- 5. DROP DEPENDENT POLICIES (Required to drop column)

-- Table: learners
DROP POLICY IF EXISTS "Parents can manage their learners" ON public.learners;
DROP POLICY IF EXISTS "Gestión total de alumnos propios" ON public.learners;
DROP POLICY IF EXISTS "Professors can manage their own students" ON public.learners;
DROP POLICY IF EXISTS "Teachers can view their own students" ON public.learners; -- From previous attempt

-- Table: learner_progress
DROP POLICY IF EXISTS "Parents can managed progress" ON public.learner_progress;

-- Table: submissions
DROP POLICY IF EXISTS "Gestión total de obras propias" ON public.submissions;

-- Table: learner_achievements
DROP POLICY IF EXISTS "Padres ven logros de hijos" ON public.learner_achievements;
DROP POLICY IF EXISTS "Professors can view achievements of their students" ON public.learner_achievements;

-- Table: path_nodes
DROP POLICY IF EXISTS "Parents can view their children path nodes" ON public.path_nodes;

-- Table: exams
DROP POLICY IF EXISTS "Students can view assigned exams" ON public.exams;

-- Table: exam_assignments
DROP POLICY IF EXISTS "Professors can manage assignments for their students" ON public.exam_assignments;


-- 6. Clean up learners table (NOW SAFE)
ALTER TABLE public.learners DROP COLUMN IF EXISTS teacher_id;


-- 7. RECREATE POLICIES (M:N Logic)

-- 7.1 Teacher Student Mapping Policies
CREATE POLICY "Admins can manage all mappings"
ON public.teacher_student_mapping FOR ALL
USING (public.is_admin());

CREATE POLICY "Teachers can view their mappings"
ON public.teacher_student_mapping FOR SELECT
USING (auth.uid() = teacher_id);

-- 7.2 Learners Policies (Teachers see linked students)
CREATE POLICY "Teachers can manage assigned students"
ON public.learners FOR ALL
USING (
    EXISTS (
        SELECT 1 FROM public.teacher_student_mapping tsm
        WHERE tsm.student_id = public.learners.id AND tsm.teacher_id = auth.uid()
    )
    OR public.is_admin()
);

-- 7.3 Learner Progress (Teachers see progress of linked students)
-- Assuming learner_progress has 'learner_id'
CREATE POLICY "Teachers can manage progress of assigned students"
ON public.learner_progress FOR ALL
USING (
    EXISTS (
        SELECT 1 FROM public.teacher_student_mapping tsm
        WHERE tsm.student_id = public.learner_progress.learner_id
        AND tsm.teacher_id = auth.uid()
    )
    OR public.is_admin()
);

-- 7.4 Submissions (Teachers see submissions of linked students)
-- Assuming submissions has 'student_id' (based on user prompt context) or 'learner_id' 
-- Defaulting to logic: verifying link between auth user (teacher) and submission owner
CREATE POLICY "Gestión total de obras de alumnos asignados"
ON public.submissions FOR ALL
USING (
    EXISTS (
        SELECT 1 FROM public.teacher_student_mapping tsm
        WHERE tsm.student_id = public.submissions.learner_id
        AND tsm.teacher_id = auth.uid()
    )
    OR public.is_admin()
);

-- 7.5 Learner Achievements
CREATE POLICY "Teachers can view achievements of assigned students"
ON public.learner_achievements FOR ALL
USING (
    EXISTS (
        SELECT 1 FROM public.teacher_student_mapping tsm
        WHERE tsm.student_id = public.learner_achievements.learner_id
        AND tsm.teacher_id = auth.uid()
    )
    OR public.is_admin()
);

-- 7.6 Exam Assignments
CREATE POLICY "Professors can manage assignments for assigned students"
ON public.exam_assignments FOR ALL
USING (
    EXISTS (
        SELECT 1 FROM public.teacher_student_mapping tsm
        WHERE tsm.student_id = public.exam_assignments.student_id
        AND tsm.teacher_id = auth.uid()
    )
    OR public.is_admin()
);

-- 7.7 Exams (Students view exams from their teachers)
CREATE POLICY "Students can view exams from their teachers"
ON public.exams FOR SELECT
USING (
    EXISTS (
        SELECT 1 FROM public.teacher_student_mapping tsm
        WHERE tsm.teacher_id = public.exams.creator_id
        AND tsm.student_id = auth.uid()
    )
    OR public.is_admin()
    OR auth.uid() = creator_id -- Teachers see their own
);

COMMIT;
