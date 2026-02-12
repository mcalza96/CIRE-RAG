-- ============================================================================
-- MIGRATION: Canvas-Lite Infrastructure (Phase 1)
-- Descripción: Implementa el almacenamiento de materiales de curso y la 
--              infraestructura de adjuntos para lecciones.
-- Fecha: 2026-02-26
-- ============================================================================

BEGIN;

-- 1. Storage Configuration (Bucket: course-materials)
-- ----------------------------------------------------------------------------

-- Insertar el bucket si no existe (Idempotencia)
INSERT INTO storage.buckets (id, name, public)
SELECT 'course-materials', 'course-materials', false
WHERE NOT EXISTS (
    SELECT 1 FROM storage.buckets WHERE id = 'course-materials'
);

-- Limpieza preventiva de políticas para evitar duplicados
DO $$ 
BEGIN 
    DROP POLICY IF EXISTS "course_materials_staff_all" ON storage.objects;
    DROP POLICY IF EXISTS "course_materials_student_read" ON storage.objects;
END $$;

-- Política 1: Acceso completo para Staff (Gestión de Materiales)
CREATE POLICY "course_materials_staff_all" ON storage.objects
FOR ALL TO authenticated
USING (
    bucket_id = 'course-materials' AND 
    public.is_staff()
)
WITH CHECK (
    bucket_id = 'course-materials' AND 
    public.is_staff()
);

-- Política 2: Lectura para Estudiantes asociados al curso de la lección
-- Nota: Se asume que el path del archivo sigue la convención 'lessons/{lesson_id}/...'
CREATE POLICY "course_materials_student_read" ON storage.objects
FOR SELECT TO authenticated
USING (
    bucket_id = 'course-materials' AND (
        public.is_staff() OR
        EXISTS (
            SELECT 1 FROM public.lessons l
            JOIN public.courses c ON c.id = l.course_id
            JOIN public.teacher_student_mapping tsm ON tsm.teacher_id = c.teacher_id
            WHERE tsm.student_id = auth.uid()
            AND (storage.objects.name LIKE 'lessons/' || l.id || '/%')
        )
    )
);

-- 2. New Entity: lesson_attachments
-- ----------------------------------------------------------------------------

DO $$ 
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'attachment_type_enum') THEN
        CREATE TYPE public.attachment_type_enum AS ENUM ('pdf', 'ppt', 'video', 'link');
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS public.lesson_attachments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lesson_id UUID NOT NULL REFERENCES public.lessons(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    resource_type public.attachment_type_enum NOT NULL,
    size_bytes BIGINT,
    storage_path TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Habilitar RLS
ALTER TABLE public.lesson_attachments ENABLE ROW LEVEL SECURITY;

-- Políticas de RLS para lesson_attachments
DO $$ 
BEGIN 
    DROP POLICY IF EXISTS "lesson_attachments_staff_management" ON public.lesson_attachments;
    DROP POLICY IF EXISTS "lesson_attachments_student_read" ON public.lesson_attachments;
END $$;

-- Gestión completa para el Staff
CREATE POLICY "lesson_attachments_staff_management" ON public.lesson_attachments
FOR ALL TO authenticated
USING (public.is_staff())
WITH CHECK (public.is_staff());

-- Lectura para estudiantes inscritos
CREATE POLICY "lesson_attachments_student_read" ON public.lesson_attachments
FOR SELECT TO authenticated
USING (
    EXISTS (
        SELECT 1 FROM public.lessons l
        JOIN public.courses c ON c.id = l.course_id
        JOIN public.teacher_student_mapping tsm ON tsm.teacher_id = c.teacher_id
        WHERE l.id = public.lesson_attachments.lesson_id
        AND tsm.student_id = auth.uid()
    )
);

-- 3. Refactoring: micro_assessments (Open/Closed Extension)
-- ----------------------------------------------------------------------------

-- Añadir competency_id si no existe
DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' 
        AND table_name = 'micro_assessments' 
        AND column_name = 'competency_id'
    ) THEN
        ALTER TABLE public.micro_assessments 
        ADD COLUMN competency_id UUID REFERENCES public.competency_nodes(id) ON DELETE SET NULL;
    END IF;
END $$;

-- Índice para optimizar el grafo de conocimiento
CREATE INDEX IF NOT EXISTS idx_micro_assessments_competency 
ON public.micro_assessments(competency_id);

-- 4. Documentation & Metadata
-- ----------------------------------------------------------------------------

COMMENT ON TABLE public.lesson_attachments IS 
'Metadatos de recursos pedagógicos adjuntos a una lección. Sigue el principio de Single Responsibility.';

COMMENT ON COLUMN public.micro_assessments.competency_id IS 
'Enlace vital entre Micro-Evaluaciones (Tickets de Salida) y la Matriz Forense de Competencias.';

COMMIT;
