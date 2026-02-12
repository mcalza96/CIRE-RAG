-- =============================================================================
-- MIGRACIÓN: ENFORCE EXAM IMMUTABILITY (Forensic Hardening)
-- Fecha: 2026-01-07
-- Descripción: Implementa triggers de seguridad estricta para garantizar que
--              la "Firma Pedagógica" (config_snapshot) de un intento no sea alterada.
-- =============================================================================

BEGIN;

-- 1. Función de Trigger para Inmutabilidad de Intentos
--    Bloquea modificaciones críticas si el examen ya comenzó.
CREATE OR REPLACE FUNCTION public.enforce_exam_inmutability()
RETURNS TRIGGER
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    -- Permitir solo actualizaciones de estado o resultados, 
    -- NUNCA de la definición del examen (config_snapshot) o identidad (learner/exam).
    
    -- Si el intento ya tiene un estado avanzado (IN_PROGRESS, COMPLETED, ABANDONED)
    IF OLD.status IN ('IN_PROGRESS', 'COMPLETED', 'ABANDONED') THEN
        
        -- Regla 1: Inmutabilidad de la Configuración (Preguntas y Matriz Q original)
        IF NEW.config_snapshot IS DISTINCT FROM OLD.config_snapshot THEN
            RAISE EXCEPTION 'Forensic Lock: Cannot modify exam configuration (config_snapshot) after attempt start. Violation of immutable audit trail.';
        END IF;

        -- Regla 2: Inmutabilidad de la Identidad
        IF NEW.learner_id IS DISTINCT FROM OLD.learner_id THEN
            RAISE EXCEPTION 'Forensic Lock: Cannot transfer attempt ownership (learner_id is immutable).';
        END IF;

        IF NEW.exam_id IS DISTINCT FROM OLD.exam_id THEN
            RAISE EXCEPTION 'Forensic Lock: Cannot reassign attempt to different exam (exam_id is immutable).';
        END IF;
    END IF;

    -- Regla 3: Si está COMPLETADO, bloquear casi todo excepto metadatos de auditoría o recalibración
    IF OLD.status = 'COMPLETED' THEN
        -- Permitir cambios SOLAMENTE en:
        -- - applied_mutations (Forensic Log)
        -- - results_cache (Recalibración)
        -- - metadata (Flags de auditoría)
        -- - updated_at
        
        -- Bloquear cambios en current_state (las respuestas del alumno son finales)
        IF NEW.current_state IS DISTINCT FROM OLD.current_state THEN
             RAISE EXCEPTION 'Forensic Lock: Cannot modify student answers (current_state) after completion.';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 2. Aplicar Trigger a la tabla exam_attempts
DROP TRIGGER IF EXISTS trg_enforce_exam_immutability ON public.exam_attempts;

CREATE TRIGGER trg_enforce_exam_immutability
    BEFORE UPDATE ON public.exam_attempts
    FOR EACH ROW
    EXECUTE FUNCTION public.enforce_exam_inmutability();

COMMIT;
