-- =============================================================================
-- MIGRACIÓN: FIX STUDENT RLS (v1.0)
-- Descripción: Habilita el acceso de lectura para estudiantes en sus propias
--              cohortes, membresías y cursos. Corrección de dashboard vacío.
-- Fecha: 2026-01-23
-- =============================================================================

BEGIN;

-- 1. Limpieza preventiva de políticas antiguas o conflictivas
DROP POLICY IF EXISTS "Estudiantes ven sus inscripciones" ON public.cohort_members;
DROP POLICY IF EXISTS "fix_cm_isolation" ON public.cohort_members;
DROP POLICY IF EXISTS "fix_cohorts_isolation" ON public.cohorts;
DROP POLICY IF EXISTS "fix_courses_read" ON public.courses;
DROP POLICY IF EXISTS "Student_View_Own_Membership" ON public.cohort_members;
DROP POLICY IF EXISTS "Student_View_Joined_Cohorts" ON public.cohorts;
DROP POLICY IF EXISTS "Student_View_Joined_Courses" ON public.courses;

-- 2. Habilitar RLS en las tablas (Idempotente)
ALTER TABLE public.cohort_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cohorts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.courses ENABLE ROW LEVEL SECURITY;

-- 3. Política Maestra en COHORT_MEMBERS: El estudiante ve su propia fila
--    Permite que el estudiante vea que ES miembro de una cohorte.
CREATE POLICY "Student_View_Own_Membership"
ON public.cohort_members
FOR SELECT
TO authenticated
USING (
    student_id = auth.uid() OR public.is_admin()
);

-- 4. Política en COHORTS: Ver Cohortes donde soy miembro
--    Necesario para hacer JOIN y ver datos de la cohorte.
CREATE POLICY "Student_View_Joined_Cohorts"
ON public.cohorts
FOR SELECT
TO authenticated
USING (
    EXISTS (
        SELECT 1 FROM public.cohort_members cm
        WHERE cm.cohort_id = public.cohorts.id
        AND cm.student_id = auth.uid()
    ) OR public.is_admin() OR teacher_id = auth.uid()
);

-- 5. Política en COURSES: Ver Cursos de mis cohortes
--    Necesario para ver el título del curso en el dashboard.
CREATE POLICY "Student_View_Joined_Courses"
ON public.courses
FOR SELECT
TO authenticated
USING (
    EXISTS (
        SELECT 1 FROM public.cohorts c
        JOIN public.cohort_members cm ON cm.cohort_id = c.id
        WHERE c.course_id = public.courses.id
        AND cm.student_id = auth.uid()
    ) OR public.is_admin() OR teacher_id = auth.uid()
);

COMMIT;
