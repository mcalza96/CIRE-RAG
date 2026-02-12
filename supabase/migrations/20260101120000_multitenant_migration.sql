-- Migration: Multi-tenant Institutional Model
-- Description: Renames terminology from Parent/Child to Teacher/Student and implements isolation.

-- 1. Migrate roles
-- Note: 'teacher' value must have been added in a previous migration/transaction.

-- Update existing 'user' roles to 'teacher'
UPDATE public.profiles 
SET role = 'teacher' 
WHERE role = 'user';

-- 2. Refactor learners table
-- Rename parent_id to teacher_id
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'learners' AND column_name = 'parent_id') THEN
        ALTER TABLE public.learners RENAME COLUMN parent_id TO teacher_id;
    END IF;
END $$;

-- Update table description
COMMENT ON TABLE public.learners IS 'Representa a los Estudiantes en el sistema institucional.';
COMMENT ON COLUMN public.learners.teacher_id IS 'ID del Profesor (Tenant) responsable del estudiante.';

-- 3. Refactor feedback_messages table
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'feedback_messages' AND column_name = 'parent_id') THEN
        ALTER TABLE public.feedback_messages RENAME COLUMN parent_id TO teacher_id;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'feedback_messages' AND column_name = 'is_read_by_parent') THEN
        ALTER TABLE public.feedback_messages RENAME COLUMN is_read_by_parent TO is_read_by_teacher;
    END IF;
END $$;

COMMENT ON COLUMN public.feedback_messages.teacher_id IS 'ID del Profesor que envió o recibió el mensaje.';

-- 4. Create exam_assignments table
CREATE TABLE IF NOT EXISTS public.exam_assignments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exam_id UUID NOT NULL REFERENCES public.exams(id) ON DELETE CASCADE,
    student_id UUID NOT NULL REFERENCES public.learners(id) ON DELETE CASCADE,
    assigned_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(exam_id, student_id)
);

-- 5. Row Level Security (RLS) Blindaje

-- Enable RLS
ALTER TABLE public.exam_assignments ENABLE ROW LEVEL SECURITY;

-- Learners (Students) Policies
DROP POLICY IF EXISTS "Professors can manage their own students" ON public.learners;
DROP POLICY IF EXISTS "Padres ven logros de sus alumnos" ON public.learner_achievements; -- Cleanup old

CREATE POLICY "Professors can manage their own students"
ON public.learners FOR ALL TO authenticated
USING (teacher_id = auth.uid() OR public.is_admin())
WITH CHECK (teacher_id = auth.uid() OR public.is_admin());

-- Learner Achievements Policies
DROP POLICY IF EXISTS "Professors can view achievements of their students" ON public.learner_achievements;
CREATE POLICY "Professors can view achievements of their students"
ON public.learner_achievements FOR SELECT TO authenticated
USING (
    learner_id IN (SELECT id FROM public.learners WHERE teacher_id = auth.uid()) OR 
    public.is_admin()
);

-- Feedback Messages Policies
DROP POLICY IF EXISTS "Padres ven mensajes de sus alumnos" ON public.feedback_messages;
DROP POLICY IF EXISTS "Professors can manage feedback for their students" ON public.feedback_messages;
CREATE POLICY "Professors can manage feedback for their students"
ON public.feedback_messages FOR ALL TO authenticated
USING (teacher_id = auth.uid() OR public.is_admin())
WITH CHECK (teacher_id = auth.uid() OR public.is_admin());

-- Exams Policies (Update for Teachers)
DROP POLICY IF EXISTS "Professors can manage their own exams" ON public.exams;
CREATE POLICY "Professors can manage their own exams"
ON public.exams FOR ALL TO authenticated
USING (creator_id = auth.uid() OR public.is_admin())
WITH CHECK (creator_id = auth.uid() OR public.is_admin());

-- Student Access to Exams (via Assignments)
DROP POLICY IF EXISTS "Students can view assigned exams" ON public.exams;
CREATE POLICY "Students can view assigned exams"
ON public.exams FOR SELECT TO authenticated
USING (
    EXISTS (
        SELECT 1 FROM public.exam_assignments ea
        JOIN public.learners l ON ea.student_id = l.id
        WHERE ea.exam_id = public.exams.id
        AND l.teacher_id = auth.uid()
    ) OR public.is_admin()
);

-- Exam Assignments Policies
DROP POLICY IF EXISTS "Professors can manage assignments for their students" ON public.exam_assignments;
CREATE POLICY "Professors can manage assignments for their students"
ON public.exam_assignments FOR ALL TO authenticated
USING (
    EXISTS (
        SELECT 1 FROM public.learners l 
        WHERE l.id = student_id AND l.teacher_id = auth.uid()
    ) OR public.is_admin()
)
WITH CHECK (
    EXISTS (
        SELECT 1 FROM public.learners l 
        WHERE l.id = student_id AND l.teacher_id = auth.uid()
    ) OR public.is_admin()
);
