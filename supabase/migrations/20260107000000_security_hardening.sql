-- =============================================================================
-- MIGRACIÓN: SECURITY HARDENING (RPCs & Assignment Validation)
-- Fecha: 2026-01-07
-- Descripción: Inyecta cláusulas de existencia obligatoria en exam_assignments
--              para evitar acceso no autorizado a exámenes no asignados.
-- =============================================================================

BEGIN;

-- 1. Redefinir `get_active_attempt_secure` con validación de asignación
DROP FUNCTION IF EXISTS public.get_active_attempt_secure(UUID, UUID);

CREATE OR REPLACE FUNCTION public.get_active_attempt_secure(
    p_exam_id UUID,
    p_learner_id UUID
)
RETURNS SETOF public.exam_attempts
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    -- A. Validación de Identidad (Authentication Guard)
    IF (auth.uid() != p_learner_id) AND (NOT public.is_admin()) THEN
        RAISE EXCEPTION 'Unauthorized: Identity mismatch in secure attempt retrieval.';
    END IF;

    -- B. Validación de Asignación (Pedagogical Guard)
    -- Regla: No puedes intentar un examen que no se te ha asignado.
    -- Excepción: Admins pueden probar sin asignación explícita (opcional, aquí somos estrictos salvo admin).
    IF NOT public.is_admin() THEN
        IF NOT EXISTS (
            SELECT 1 FROM public.exam_assignments 
            WHERE exam_id = p_exam_id 
            AND student_id = p_learner_id
        ) THEN
            RAISE EXCEPTION 'Unauthorized: Exam not assigned to this learner.';
        END IF;
    END IF;

    RETURN QUERY
    SELECT *
    FROM public.exam_attempts
    WHERE exam_id = p_exam_id
      AND learner_id = p_learner_id
      AND status = 'IN_PROGRESS'
    LIMIT 1;
END;
$$ LANGUAGE plpgsql;


-- 2. Redefinir `create_exam_attempt_secure` con validación de asignación
DROP FUNCTION IF EXISTS public.create_exam_attempt_secure(UUID, UUID, JSONB);

CREATE OR REPLACE FUNCTION public.create_exam_attempt_secure(
    p_exam_id UUID,
    p_learner_id UUID,
    p_config_snapshot JSONB
)
RETURNS SETOF public.exam_attempts
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    -- A. Validación de Identidad
    IF (auth.uid() != p_learner_id) AND (NOT public.is_admin()) THEN
        RAISE EXCEPTION 'Unauthorized: Identity mismatch in secure attempt creation.';
    END IF;

    -- B. Validación de Asignación
    IF NOT public.is_admin() THEN
        IF NOT EXISTS (
            SELECT 1 FROM public.exam_assignments 
            WHERE exam_id = p_exam_id 
            AND student_id = p_learner_id
        ) THEN
            RAISE EXCEPTION 'Unauthorized: Cannot create attempt for unassigned exam.';
        END IF;
    END IF;

    -- B.2 Validación de Estado del Examen (Debe estar PUBLISHED)
    -- Para evitar fugas de información de draft exams.
    IF NOT EXISTS (
        SELECT 1 FROM public.exams
        WHERE id = p_exam_id
        AND status = 'PUBLISHED'
    ) AND NOT public.is_admin() THEN
         RAISE EXCEPTION 'Unauthorized: Exam is not published.';
    END IF;

    RETURN QUERY
    INSERT INTO public.exam_attempts (
        exam_id,
        learner_id,
        status,
        config_snapshot,
        current_state,
        applied_mutations,
        results_cache
    ) VALUES (
        p_exam_id,
        p_learner_id,
        'IN_PROGRESS',
        p_config_snapshot,
        '{}'::jsonb,
        '[]'::jsonb,
        '{}'::jsonb
    )
    RETURNING *;
END;
$$ LANGUAGE plpgsql;

-- 3. Permisos (Re-aplicar grants por seguridad)
REVOKE EXECUTE ON FUNCTION public.get_active_attempt_secure FROM public;
GRANT EXECUTE ON FUNCTION public.get_active_attempt_secure TO authenticated;

REVOKE EXECUTE ON FUNCTION public.create_exam_attempt_secure FROM public;
GRANT EXECUTE ON FUNCTION public.create_exam_attempt_secure TO authenticated;

COMMIT;
