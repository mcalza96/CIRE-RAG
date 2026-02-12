-- Hotfix: exams_read_access policy update
-- Date: 2026-01-17
-- Description: Expands read access for exams to include direct assignments (exam_assignments table), 
-- fixing the issue where students could not see exams assigned to them if they weren't in the teacher's cohort (M:N mapping).

DROP POLICY IF EXISTS "exams_read_access" ON public.exams;

CREATE POLICY "exams_read_access" ON public.exams FOR SELECT TO authenticated
USING (
    creator_id = auth.uid() OR 
    public.is_admin() OR 
    -- Acceso vía matriculación general (M:N)
    EXISTS(SELECT 1 FROM public.teacher_student_mapping tsm WHERE tsm.teacher_id = public.exams.creator_id AND tsm.student_id = auth.uid()) OR
    -- NUEVO: Acceso vía asignación directa de examen
    EXISTS(SELECT 1 FROM public.exam_assignments ea WHERE ea.exam_id = public.exams.id AND ea.student_id = auth.uid())
);
