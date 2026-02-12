-- 1. Limpieza preventiva (por si creaste políticas con nombres incorrectos)
DROP POLICY IF EXISTS "Estudiantes ven sus inscripciones" ON public.cohort_members;
-- DROP POLICY IF EXISTS "Estudiantes ven sus inscripciones" ON public.cohort_students; -- Table does not exist, skipping cleanup
-- Also drop policies created by previous emergency scripts
DROP POLICY IF EXISTS "fix_cm_isolation" ON public.cohort_members;
DROP POLICY IF EXISTS "fix_cohorts_isolation" ON public.cohorts;
DROP POLICY IF EXISTS "fix_courses_read" ON public.courses;


-- 2. Habilitar RLS en la tabla correcta
ALTER TABLE public.cohort_members ENABLE ROW LEVEL SECURITY;

-- 3. Política Maestra: El estudiante ve su propia fila
CREATE POLICY "Student_View_Own_Membership"
ON public.cohort_members
FOR SELECT
USING (
    student_id = auth.uid() OR public.is_admin()
);

-- 4. Política de Cascada: Ver Cohortes
CREATE POLICY "Student_View_Joined_Cohorts"
ON public.cohorts
FOR SELECT
USING (
    EXISTS (
        SELECT 1 FROM public.cohort_members cm
        WHERE cm.cohort_id = public.cohorts.id
        AND cm.student_id = auth.uid()
    ) OR public.is_admin() OR teacher_id = auth.uid()
);

-- 5. Política de Cascada: Ver Cursos
CREATE POLICY "Student_View_Joined_Courses"
ON public.courses
FOR SELECT
USING (
    EXISTS (
        SELECT 1 FROM public.cohorts c
        JOIN public.cohort_members cm ON cm.cohort_id = c.id
        WHERE c.course_id = public.courses.id
        AND cm.student_id = auth.uid()
    ) OR public.is_admin() OR teacher_id = auth.uid()
);
