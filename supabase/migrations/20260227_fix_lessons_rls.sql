-- ============================================================================
-- MIGRATION: Fix Lessons RLS for Students
-- Descripción: Corrección crítica de permisos. Permite que los estudiantes vinculados
--              a un profesor puedan ver las lecciones de sus cursos.
-- Fecha: 2026-02-27
-- ============================================================================

BEGIN;

-- Eliminar política anterior si existe (para evitar conflictos o restricciones excesivas)
DROP POLICY IF EXISTS "student_view_lessons" ON public.lessons;
DROP POLICY IF EXISTS "Cualquier usuario puede ver lecciones" ON public.lessons;

-- Crear política correcta basada en teacher_student_mapping
CREATE POLICY "student_view_lessons" ON public.lessons
FOR SELECT TO authenticated
USING (
    public.is_staff() OR          -- Profesores (Staff) ven todo
    public.is_admin() OR          -- Admins ven todo
    EXISTS (                      -- Estudiantes ven si están vinculados al profesor del curso
        SELECT 1 FROM public.courses c
        JOIN public.teacher_student_mapping tsm ON tsm.teacher_id = c.teacher_id
        WHERE c.id = public.lessons.course_id
        AND tsm.student_id = auth.uid()
    )
);

COMMIT;
