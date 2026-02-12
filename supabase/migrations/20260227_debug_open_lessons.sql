-- ============================================================================
-- DEBUG MIGRATION: OPEN LESSONS ACCESS
-- Descripción: Política temporal ultra-permisiva para confirmar si RLS es el problema.
-- ADVERTENCIA: Usar solo para debugging.
-- Fecha: 2026-02-27
-- ============================================================================

BEGIN;

DROP POLICY IF EXISTS "student_view_lessons" ON public.lessons;
DROP POLICY IF EXISTS "Cualquier usuario puede ver lecciones" ON public.lessons;
DROP POLICY IF EXISTS "debug_open_lessons" ON public.lessons;

-- OPEN THE GATES
CREATE POLICY "debug_open_lessons" ON public.lessons
FOR SELECT TO authenticated
USING (true);

COMMIT;
