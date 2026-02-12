-- Optimización de Índices Faltantes (Epic 4)
-- Fecha: 2026-01-17

-- 1. Índice crítico para dashboard de estudiantes (Assignments por alumno)
-- Actualmente solo existe UNIQUE(exam_id, student_id), lo cual no optimiza queries por student_id.
CREATE INDEX IF NOT EXISTS idx_exam_assignments_student 
ON public.exam_assignments (student_id);

-- 2. Índice para filtrado de contenido por creador (My Competencies)
CREATE INDEX IF NOT EXISTS idx_competency_nodes_creator 
ON public.competency_nodes (created_by);

-- 3. Índice para búsqueda de perfiles por email (Auth flows)
CREATE INDEX IF NOT EXISTS idx_profiles_email 
ON public.profiles (email);
