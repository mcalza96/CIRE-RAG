-- ============================================================================
-- MIGRATION: MicroAssessments Idempotency
-- Descripción: Agrega hash de contenido para evitar regeneración de preguntas duplicadas.
-- Fecha: 2026-02-15
-- ============================================================================

BEGIN;

-- 1. Agregar columna content_hash
ALTER TABLE public.micro_assessments
  ADD COLUMN IF NOT EXISTS content_hash text;

-- 2. Crear índice único para idempotencia
-- Permite múltiples versiones de evaluaciones para una lección SI el contenido cambia (hash diferente)
-- Pero previene duplicados para el MISMO contenido.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_micro_assessments_lesson_hash_type
ON public.micro_assessments (lesson_id, content_hash, type);

COMMENT ON COLUMN public.micro_assessments.content_hash IS 
'Hash SHA-256 del contenido de la lección al momento de generar la pregunta. Usado para idempotencia.';

COMMIT;
