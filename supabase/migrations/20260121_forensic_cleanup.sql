-- Forensic Cleanup & Hardening Migration
-- Date: 2026-01-21
-- Description: Cleans up zombie exam attempts and optimizes lookup performance.

-- 1. Index Tuning: Acelerar búsqueda de intentos activos en el Lobby
CREATE INDEX IF NOT EXISTS idx_attempts_lookup 
ON public.exam_attempts (exam_id, learner_id, status);

-- 2. Limpieza Forense de Intentos Corruptos (Null Timestamps)
-- Borramos intentos que técnicamente están "en progreso" pero no tienen marca de actividad,
-- lo cual es un estado inconsistente (probablemente fallos de inicialización antiguos).
DELETE FROM public.exam_attempts 
WHERE status = 'IN_PROGRESS' 
AND last_active_at IS NULL;

-- 3. Limpieza de Intentos Abandonados (Stalled > 24h sin logs)
-- Borra intentos que llevan >24h abiertos pero NO tienen telemetría asociada.
-- Esto indica que el usuario abrió el examen pero nunca interactuó.
DELETE FROM public.exam_attempts
WHERE status = 'IN_PROGRESS'
AND last_active_at < NOW() - INTERVAL '24 hours'
AND NOT EXISTS (
    SELECT 1 FROM public.telemetry_logs 
    WHERE telemetry_logs.attempt_id = public.exam_attempts.id
);

-- 4. Aseguramiento de Integridad Referencial
-- Intentamos asegurar que la FK tenga CASCADE para evitar huérfanos futuros.
-- Usamos un bloque anónimo para hacerlo de manera segura (idempotente).
DO $$
BEGIN
    -- Solo si la FK existe sin cascade, idealmente deberíamos recrearla, 
    -- pero para este hotfix asumimos que si borramos intentos, los hijos deben irse.
    -- Si la tabla telemetry_logs fue creada correctamente con CASCADE, esto es automático.
    -- Si no, esta operación de DELETE fallaría si hay hijos.
    
    -- Verificación proactiva irrelevante si confiamos en el esquema, pero para "Hardening":
    IF EXISTS (
        SELECT 1 
        FROM information_schema.referential_constraints 
        WHERE constraint_name = 'telemetry_logs_attempt_id_fkey'
        AND delete_rule != 'CASCADE'
    ) THEN
        ALTER TABLE public.telemetry_logs
        DROP CONSTRAINT telemetry_logs_attempt_id_fkey,
        ADD CONSTRAINT telemetry_logs_attempt_id_fkey
        FOREIGN KEY (attempt_id)
        REFERENCES public.exam_attempts(id)
        ON DELETE CASCADE;
    END IF;
END $$;
