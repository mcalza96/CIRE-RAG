-- ============================================================================
-- MIGRATION: BlockNote Content Integration
-- ============================================================================
-- Objetivo: Añadir soporte para contenido estructurado (JSONB) en lecciones.
-- Fecha: 2026-02-01
-- ============================================================================

-- 1. Añadir columna content_blocks a la tabla lessons
ALTER TABLE public.lessons
ADD COLUMN IF NOT EXISTS content_blocks JSONB DEFAULT '[]'::JSONB;

-- 2. Asegurar que las políticas RLS permitan actualizar esta columna
-- (Las políticas existentes de UPDATE para dueños del curso deberían cubrirlo,
-- pero verificamos permisos explícitos si hay roles de columna).
-- Nota: Supabase RLS aplica a nivel de fila por defecto, no columna, a menos que se especifique.
-- Si ya existen políticas UPDATE para 'lessons', cubrirán la nueva columna automáticamente.

COMMENT ON COLUMN public.lessons.content_blocks IS 
'Contenido estructurado de la lección en formato BlockNote (JSONB). Reemplaza gradualmente a description para el contenido principal.';
