-- ============================================================================
-- MIGRATION: Remediation Infrastructure (Phase 3)
-- Descripción: Añade columna de diagnóstico y tabla de contenido remedial.
-- Fecha: 2026-02-19
-- ============================================================================

BEGIN;

-- 1. Actualizar micro_submissions
-- ----------------------------------------------------------------------------
DO $$ 
BEGIN 
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'micro_submissions' AND column_name = 'diagnosis') THEN
        ALTER TABLE public.micro_submissions 
        ADD COLUMN diagnosis TEXT CHECK (diagnosis IN ('MASTERY', 'SLIP', 'GAP', 'GUESS'));
    END IF;
END $$;

-- 2. Tabla: remedial_content (Almacena las cápsulas generadas por IA)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.remedial_content (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    submission_id UUID NOT NULL REFERENCES public.micro_submissions(id) ON DELETE CASCADE,
    concept_id TEXT, -- Puede venir del payload de la micro-evaluación
    content JSONB NOT NULL, -- La cápsula generada (misconceptionAnalysis, explanationBlocks, analogy, etc.)
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    -- Un contenido remedial por cada intento/sumisión
    CONSTRAINT unique_submission_remediation UNIQUE(submission_id)
);

-- 3. Índices Estratégicos
-- ----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_remedial_content_submission ON public.remedial_content(submission_id);

-- 4. Row Level Security (RLS)
-- ----------------------------------------------------------------------------
ALTER TABLE public.remedial_content ENABLE ROW LEVEL SECURITY;

-- Lectura: Estudiantes pueden ver el contenido remedial de SUS propias sumisiones
CREATE POLICY "remedial_content_read_access" ON public.remedial_content
FOR SELECT TO authenticated
USING (
    EXISTS (
        SELECT 1 FROM public.micro_submissions s
        WHERE s.id = public.remedial_content.submission_id
        AND s.student_id = auth.uid()
    ) OR
    public.is_staff() -- Profesores pueden ver por diagnóstico
);

-- Gestión: Solo el sistema (service_role) o Staff puede insertar/limpiar
-- Nota: La Server Action se ejecuta con permisos elevados o como el usuario,
-- pero el sistema disparará la generación.
CREATE POLICY "remedial_content_system_insert" ON public.remedial_content
FOR INSERT TO authenticated
WITH CHECK (
    EXISTS (
        SELECT 1 FROM public.micro_submissions s
        WHERE s.id = public.remedial_content.submission_id
        AND s.student_id = auth.uid()
    ) OR public.is_staff()
);

COMMIT;
