-- ========================================================
-- CISRE - OPTIMIZACIÓN DE ÍNDICES (v1.0)
-- ========================================================
-- DESCRIPCIÓN: 
--   Añade índices críticos para el rendimiento de RLS,
--   Joins institucionales y auditoría forense.
-- FECHA: 2026-01-17
-- ========================================================

BEGIN;

-- 1. AISLAMIENTO INSTITUCIONAL (RLS & JOINS)
-- Mejora el rendimiento del mapeo M:N al buscar por estudiante
CREATE INDEX IF NOT EXISTS idx_tsm_student ON public.teacher_student_mapping (student_id);

-- Mejora el filtrado de cohortes por docente (RLS)
CREATE INDEX IF NOT EXISTS idx_cohorts_teacher ON public.cohorts (teacher_id);

-- Mejora las consultas de pertenencia a cohorte por estudiante
CREATE INDEX IF NOT EXISTS idx_cohort_members_student ON public.cohort_members (student_id);


-- 2. MOTOR DE EVALUACIÓN (HOT PATHS)
-- Optimiza el listado de exámenes por docente y estado
CREATE INDEX IF NOT EXISTS idx_exams_creator_status ON public.exams (creator_id, status);

-- Optimiza joins y filtrado RLS en intentos
CREATE INDEX IF NOT EXISTS idx_exam_attempts_exam ON public.exam_attempts (exam_id);
CREATE INDEX IF NOT EXISTS idx_exam_attempts_learner ON public.exam_attempts (learner_id);


-- 3. FORENSE Y TELEMETRÍA (CARGA MASIVA)
-- ÍNDICE CRÍTICO: Evita table scans en telemetry_logs durante la validación RLS
CREATE INDEX IF NOT EXISTS idx_telemetry_attempt ON public.telemetry_logs (attempt_id);

-- Optimiza el acceso al portafolio por estudiante
CREATE INDEX IF NOT EXISTS idx_submissions_learner ON public.submissions (learner_id);


-- 4. GRAFO DE CONOCIMIENTO Y AUDITORÍA
-- Optimiza la navegación por el grafo de competencias
CREATE INDEX IF NOT EXISTS idx_comp_edges_source ON public.competency_edges (source_id);
CREATE INDEX IF NOT EXISTS idx_comp_edges_target ON public.competency_edges (target_id);

-- Mejora la búsqueda de reactivos por competencia
CREATE INDEX IF NOT EXISTS idx_probes_competency ON public.diagnostic_probes (competency_id);

-- Mejora la búsqueda de opciones por reactivo y diagnósticos
CREATE INDEX IF NOT EXISTS idx_probe_options_probe ON public.probe_options (probe_id);
CREATE INDEX IF NOT EXISTS idx_probe_options_misconception ON public.probe_options (diagnoses_misconception_id);

-- Auditoría de costos de AI
CREATE INDEX IF NOT EXISTS idx_ai_usage_user ON public.ai_usage_logs (user_id);

COMMIT;
