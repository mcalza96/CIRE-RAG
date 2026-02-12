-- ============================================================================
-- DEBUG MIGRATION: OPEN MICRO ASSESSMENTS ACCESS
-- Descripción: Política temporal para que estudiantes puedan leer micro_assessments
-- ADVERTENCIA: Usar solo para debugging.
-- Fecha: 2026-02-27
-- ============================================================================

BEGIN;

DROP POLICY IF EXISTS "micro_assessments_read_access" ON public.micro_assessments;
DROP POLICY IF EXISTS "debug_open_micro_assessments" ON public.micro_assessments;

-- OPEN THE GATES for students to read micro_assessments (so count works)
CREATE POLICY "debug_open_micro_assessments" ON public.micro_assessments
FOR SELECT TO authenticated
USING (true);

COMMIT;
