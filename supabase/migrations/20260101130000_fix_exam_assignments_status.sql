-- Migration: Fix missing columns in exam_assignments
-- Description: Adds 'status' and 'origin_context' columns required by the repository.

ALTER TABLE public.exam_assignments 
ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'assigned',
ADD COLUMN IF NOT EXISTS origin_context TEXT DEFAULT 'manual';

COMMENT ON COLUMN public.exam_assignments.status IS 'Estado de la asignación (assigned, in_progress, completed)';
COMMENT ON COLUMN public.exam_assignments.origin_context IS 'Contexto de creación (standalone, manual, automated)';
