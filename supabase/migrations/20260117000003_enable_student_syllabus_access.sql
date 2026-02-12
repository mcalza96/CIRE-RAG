-- POLICY: Permitir a los estudiantes ver el syllabus de sus cursos
-- Fecha: 2026-01-14
-- Autor: CISRE Architect

DO $$ 
BEGIN
    -- Verificar si la política ya existe para evitar errores
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies 
        WHERE tablename = 'course_syllabus' 
        AND policyname = 'Students can view syllabus of enrolled courses'
    ) THEN
        CREATE POLICY "Students can view syllabus of enrolled courses"
            ON public.course_syllabus
            FOR SELECT
            USING (
                EXISTS (
                    SELECT 1 FROM public.cohort_members cm
                    JOIN public.cohorts c ON c.id = cm.cohort_id
                    WHERE c.course_id = course_syllabus.course_id
                    AND cm.student_id = auth.uid()
                )
            );
    END IF;
END $$;
