-- ========================================================
-- CISRE - SCHEMA MAESTRO CONSOLIDADO (v4.0)
-- ========================================================
-- DESCRIPCIÓN:
--   Este archivo representa el ESTADO FINAL CONSOLIDADO de la
--   arquitectura de datos de CISRE al 5 de enero de 2026.
--   Es la fuente de verdad única para nuevas instalaciones.
--
-- CARACTERÍSTICAS PRINCIPALES:
--   - Sustitución de parent/child por teacher/student.
--   - Modelo M:N vía teacher_student_mapping.
--   - Versionamiento de exámenes e inmutabilidad forense.
--   - RLS de aislamiento institucional.
--   - Vistas analíticas normalizadas.
-- ========================================================

-- 1. EXTENSIONES
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 2. TIPOS PERSONALIZADOS (ENUMS)
DO $$ 
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'app_role') THEN
        CREATE TYPE public.app_role AS ENUM ('admin', 'teacher', 'instructor', 'student');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'node_type_enum') THEN
        CREATE TYPE public.node_type_enum AS ENUM ('competency', 'misconception', 'bridge');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'relation_type_enum') THEN
        CREATE TYPE public.relation_type_enum AS ENUM ('prerequisite', 'misconception_of', 'remedies');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'exam_status_enum') THEN
        CREATE TYPE public.exam_status_enum AS ENUM ('DRAFT', 'PUBLISHED', 'ARCHIVED');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'attempt_status_enum') THEN
        CREATE TYPE public.attempt_status_enum AS ENUM ('IN_PROGRESS', 'COMPLETED', 'ABANDONED');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'probe_type_enum') THEN
        CREATE TYPE public.probe_type_enum AS ENUM ('multiple_choice_rationale', 'phenomenological_checklist');
    END IF;
END $$;

-- 3. HELPERS DE SEGURIDAD (SECURITY DEFINER)
CREATE OR REPLACE FUNCTION public.is_admin()
RETURNS BOOLEAN AS $$
BEGIN
  RETURN (
    SELECT (role = 'admin')
    FROM public.profiles
    WHERE id = auth.uid()
  );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

CREATE OR REPLACE FUNCTION public.is_staff()
RETURNS BOOLEAN AS $$
BEGIN
  RETURN (
    SELECT (role IN ('admin', 'instructor', 'teacher'))
    FROM public.profiles
    WHERE id = auth.uid()
  );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

-- 4. TABLAS NÚCLEO (IDENTIDAD Y TENANTS)

-- Perfiles de Usuario
CREATE TABLE IF NOT EXISTS public.profiles (
  id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  email TEXT NOT NULL,
  full_name TEXT,
  avatar_url TEXT,
  role public.app_role DEFAULT 'teacher',
  demographic_group TEXT, -- Para analítica de equidad
  access_type TEXT, -- Para device neutrality
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Estudiantes (Learners)
-- La columna teacher_id ha sido eliminada en favor del mapeo M:N
CREATE TABLE IF NOT EXISTS public.learners (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  display_name TEXT NOT NULL,
  avatar_url TEXT,
  level INT DEFAULT 1,
  metadata JSONB DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Mapeo M:N Profesores-Estudiantes (Institutional Isolation)
CREATE TABLE IF NOT EXISTS public.teacher_student_mapping (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    student_id UUID NOT NULL REFERENCES public.learners(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT teacher_student_mapping_unique UNIQUE (teacher_id, student_id)
);

-- Cohortes (Grupos de Estudiantes)
CREATE TABLE IF NOT EXISTS public.cohorts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    teacher_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Miembros de Cohorte
CREATE TABLE IF NOT EXISTS public.cohort_members (
    cohort_id UUID NOT NULL REFERENCES public.cohorts(id) ON DELETE CASCADE,
    student_id UUID NOT NULL REFERENCES public.learners(id) ON DELETE CASCADE,
    added_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (cohort_id, student_id)
);

-- 5. MOTOR PEDAGÓGICO (KNOWLEDGE GRAPH)

-- Nodos de Competencia
CREATE TABLE IF NOT EXISTS public.competency_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    description TEXT,
    embedding vector(1536),
    node_type public.node_type_enum NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Relaciones (Aristas)
CREATE TABLE IF NOT EXISTS public.competency_edges (
    source_id UUID REFERENCES public.competency_nodes(id) ON DELETE CASCADE,
    target_id UUID REFERENCES public.competency_nodes(id) ON DELETE CASCADE,
    relation_type public.relation_type_enum NOT NULL,
    weight FLOAT DEFAULT 1.0,
    PRIMARY KEY (source_id, target_id, relation_type)
);

-- Reactivos Diagnósticos (Probes)
CREATE TABLE IF NOT EXISTS public.diagnostic_probes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    competency_id UUID NOT NULL REFERENCES public.competency_nodes(id) ON DELETE CASCADE,
    type public.probe_type_enum NOT NULL,
    stem TEXT NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Opciones de Probes
CREATE TABLE IF NOT EXISTS public.probe_options (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    probe_id UUID NOT NULL REFERENCES public.diagnostic_probes(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    is_correct BOOLEAN NOT NULL DEFAULT FALSE,
    diagnoses_misconception_id UUID REFERENCES public.competency_nodes(id) ON DELETE SET NULL,
    feedback TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 6. MOTOR DE EVALUACIÓN Y FORENSE

-- Exámenes (Instrumentos Diagnósticos)
CREATE TABLE IF NOT EXISTS public.exams (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    description TEXT,
    creator_id UUID REFERENCES public.profiles(id) NOT NULL,
    status public.exam_status_enum DEFAULT 'DRAFT' NOT NULL,
    config_json JSONB DEFAULT '{}'::jsonb,
    q_matrix JSONB DEFAULT '[]'::jsonb,
    version INT DEFAULT 1 NOT NULL,
    parent_exam_id UUID REFERENCES public.exams(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Unique index for versions
CREATE UNIQUE INDEX IF NOT EXISTS idx_exams_parent_version 
ON public.exams (parent_exam_id, version) 
WHERE parent_exam_id IS NOT NULL;

-- Asignaciones M:N
CREATE TABLE IF NOT EXISTS public.exam_assignments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exam_id UUID NOT NULL REFERENCES public.exams(id) ON DELETE CASCADE,
    student_id UUID NOT NULL REFERENCES public.learners(id) ON DELETE CASCADE,
    assigned_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(exam_id, student_id)
);

-- Intentos de Examen (Frontera de Evidencia)
CREATE TABLE IF NOT EXISTS public.exam_attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exam_id UUID NOT NULL REFERENCES public.exams(id) ON DELETE CASCADE,
    learner_id UUID NOT NULL REFERENCES public.learners(id) ON DELETE CASCADE,
    status public.attempt_status_enum DEFAULT 'IN_PROGRESS' NOT NULL,
    config_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    current_state JSONB NOT NULL DEFAULT '{}'::jsonb,
    results_cache JSONB DEFAULT '{}'::jsonb,
    applied_mutations JSONB DEFAULT '[]'::jsonb,
    score FLOAT,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    last_active_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- Logs de Telemetría (Forensic Trace)
CREATE TABLE IF NOT EXISTS public.telemetry_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id UUID NOT NULL REFERENCES public.exam_attempts(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    payload JSONB DEFAULT '{}'::jsonb,
    timestamp TIMESTAMPTZ DEFAULT NOW()
);

-- Entregas (Portfolio)
CREATE TABLE IF NOT EXISTS public.submissions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  learner_id UUID NOT NULL REFERENCES public.learners(id) ON DELETE CASCADE,
  lesson_id UUID,
  title TEXT NOT NULL,
  file_url TEXT NOT NULL,
  thumbnail_url TEXT,
  category TEXT,
  is_public BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 7. VISTAS ANALÍTICAS (INTELLIGENCE SUITE)

-- VIEW: vw_pathology_ranking
CREATE OR REPLACE VIEW public.vw_pathology_ranking AS
WITH expanded_diagnoses AS (
    SELECT 
        e.creator_id as teacher_id,
        ea.exam_id,
        (diag->>'competencyId')::text as competency_id,
        (diag->>'state')::text as state,
        (diag->'evidence'->>'reason')::text as reason,
        (diag->'evidence'->>'confidenceScore')::float as confidence_score,
        (diag->'evidence'->>'hesitationCount')::int as hesitation_count,
        (diag->'evidence'->>'timeMs')::int as time_ms
    FROM exam_attempts ea
    JOIN exams e ON ea.exam_id = e.id
    CROSS JOIN LATERAL jsonb_array_elements(ea.results_cache->'competencyDiagnoses') as diag
    WHERE ea.status = 'COMPLETED'
)
SELECT 
    teacher_id,
    exam_id,
    competency_id,
    state,
    COUNT(*) as total_occurrences,
    AVG(confidence_score) as avg_confidence_score,
    AVG(hesitation_count) as avg_hesitation_index,
    AVG(time_ms) as avg_response_time
FROM expanded_diagnoses
WHERE state = 'MISCONCEPTION'
GROUP BY teacher_id, exam_id, competency_id, state;

-- VIEW: vw_item_health
CREATE OR REPLACE VIEW public.vw_item_health AS
WITH raw_answers AS (
    SELECT 
        e.creator_id as teacher_id,
        ea.exam_id,
        key as question_id,
        (value->>'isCorrect')::boolean as is_correct,
        (value->>'timeMs')::int as time_ms,
        (value->>'confidence')::text as confidence
    FROM exam_attempts ea
    JOIN exams e ON ea.exam_id = e.id
    CROSS JOIN LATERAL jsonb_each(ea.current_state) as answers(key, value)
    WHERE ea.status = 'COMPLETED'
),
item_stats AS (
    SELECT
        teacher_id,
        exam_id,
        question_id,
        COUNT(*) as total_responses,
        COUNT(CASE WHEN is_correct THEN 1 END) as correct_count,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY time_ms) as median_time_ms
    FROM raw_answers
    GROUP BY teacher_id, exam_id, question_id
)
SELECT
    teacher_id,
    exam_id,
    question_id,
    total_responses,
    (correct_count::float / NULLIF(total_responses, 0)) * 100 as accuracy_rate,
    median_time_ms,
    CASE
        WHEN (correct_count::float / NULLIF(total_responses, 0)) = 0 AND median_time_ms > 60000 THEN 'BROKEN'
        WHEN (correct_count::float / NULLIF(total_responses, 0)) = 1 AND median_time_ms < 5000 THEN 'TRIVIAL'
        ELSE 'HEALTHY'
    END as health_status
FROM item_stats;

-- VIEW: vw_cohort_radar
CREATE OR REPLACE VIEW public.vw_cohort_radar AS
SELECT 
    e.creator_id as teacher_id,
    ea.exam_id,
    ea.learner_id as student_id,
    (ea.results_cache->>'overallScore')::float as overall_score,
    (ea.results_cache->'calibration'->>'eceScore')::float as ece_score,
    (ea.results_cache->'behaviorProfile'->>'isImpulsive')::boolean as is_impulsive,
    (ea.results_cache->'behaviorProfile'->>'isAnxious')::boolean as is_anxious,
    CASE
        WHEN (ea.results_cache->'calibration'->>'eceScore')::float < 10 AND (ea.results_cache->>'overallScore')::float > 80 THEN 'MASTER'
        WHEN (ea.results_cache->'behaviorProfile'->>'isImpulsive')::boolean THEN 'IMPULSIVE'
        WHEN (ea.results_cache->'behaviorProfile'->>'isAnxious')::boolean THEN 'UNCERTAIN'
        WHEN (ea.results_cache->'calibration'->>'eceScore')::float > 20 THEN 'DELUSIONAL'
        ELSE 'DEVELOPING'
    END as student_archetype
FROM exam_attempts ea
JOIN exams e ON ea.exam_id = e.id
WHERE ea.status = 'COMPLETED';

-- VIEW: vw_remediation_fairness
CREATE OR REPLACE VIEW public.vw_remediation_fairness AS
SELECT 
    e.creator_id as teacher_id,
    p.demographic_group,
    p.access_type,
    COUNT(DISTINCT ea.id) as total_attempts,
    COUNT(CASE WHEN (ea.results_cache->>'overallScore')::float < 60 THEN 1 END) as failed_attempts,
    AVG((ea.results_cache->>'overallScore')::float) as avg_score,
    ROUND(
        COUNT(CASE WHEN m.value->>'action' = 'INSERT_NODE' THEN 1 END)::numeric / 
        GREATEST(COUNT(DISTINCT ea.id), 1), 2
    )::float as intervention_rate
FROM 
    public.exam_attempts ea
JOIN 
    public.exams e ON ea.exam_id = e.id
JOIN 
    public.profiles p ON ea.learner_id = p.id
LEFT JOIN 
    jsonb_array_elements(ea.applied_mutations) m ON true
WHERE 
    ea.status = 'COMPLETED'
GROUP BY 
    e.creator_id, p.demographic_group, p.access_type;

-- VIEW: vw_item_dif
CREATE OR REPLACE VIEW public.vw_item_dif AS
WITH item_performance AS (
    SELECT 
        e.creator_id as teacher_id,
        key as question_id,
        'demographic' as dimension,
        p.demographic_group as sub_group,
        AVG(CASE WHEN (value->>'isCorrect')::boolean THEN 1 ELSE 0 END) as success_rate,
        COUNT(*) as total_responses
    FROM exam_attempts ea
    JOIN exams e ON ea.exam_id = e.id
    JOIN profiles p ON ea.learner_id = p.id
    CROSS JOIN LATERAL jsonb_each(ea.current_state) as answers(key, value)
    WHERE ea.status = 'COMPLETED'
    GROUP BY e.creator_id, key, p.demographic_group
    
    UNION ALL
    
    SELECT 
        e.creator_id as teacher_id,
        key as question_id,
        'access_type' as dimension,
        p.access_type as sub_group,
        AVG(CASE WHEN (value->>'isCorrect')::boolean THEN 1 ELSE 0 END) as success_rate,
        COUNT(*) as total_responses
    FROM exam_attempts ea
    JOIN exams e ON ea.exam_id = e.id
    JOIN profiles p ON ea.learner_id = p.id
    CROSS JOIN LATERAL jsonb_each(ea.current_state) as answers(key, value)
    WHERE ea.status = 'COMPLETED'
    GROUP BY e.creator_id, key, p.access_type
)
SELECT 
    question_id,
    dimension,
    MAX(success_rate) - MIN(success_rate) as gap,
    COUNT(DISTINCT sub_group) as compared_groups,
    SUM(total_responses) as total_responses,
    CASE 
        WHEN MAX(success_rate) - MIN(success_rate) > 0.2 THEN 'CRITICAL'
        WHEN MAX(success_rate) - MIN(success_rate) > 0.1 THEN 'WARNING'
        ELSE 'OPTIMAL'
    END as status
FROM item_performance
GROUP BY question_id, dimension
HAVING MAX(success_rate) - MIN(success_rate) > 0.1 AND SUM(total_responses) >= 5;

-- 8. AUDITORÍA Y AI
CREATE TABLE IF NOT EXISTS public.ai_usage_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES auth.users(id),
    model TEXT NOT NULL,
    tokens_input INTEGER DEFAULT 0,
    tokens_output INTEGER DEFAULT 0,
    cost_estimated NUMERIC(10, 6),
    feature_used TEXT NOT NULL,
    timestamp TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.draft_exams (
    lesson_id TEXT PRIMARY KEY,
    context JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_draft_exams_updated_at ON public.draft_exams (updated_at);

-- 9. TRIGGERS Y FUNCIONES DE INTEGRIDAD

-- Sincronización de Perfiles
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO public.profiles (id, email, full_name, avatar_url, role)
  VALUES (
    new.id, 
    new.email, 
    new.raw_user_meta_data->>'full_name', 
    new.raw_user_meta_data->>'avatar_url',
    COALESCE(new.raw_user_meta_data->>'role', 'teacher')::public.app_role
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- Inmutabilidad Forense
CREATE OR REPLACE FUNCTION public.enforce_exam_inmutability()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_TABLE_NAME = 'exams' THEN
        IF (OLD.status = 'PUBLISHED') AND (NEW.status = 'PUBLISHED') THEN
            IF (NEW.config_json IS DISTINCT FROM OLD.config_json OR 
                NEW.q_matrix IS DISTINCT FROM OLD.q_matrix) THEN
                RAISE EXCEPTION 'Forensic Integrity Violation: Cannot modify a PUBLISHED exam. Please version it.';
            END IF;
        END IF;
    END IF;

    IF TG_TABLE_NAME = 'exam_attempts' THEN
        IF (NEW.config_snapshot IS DISTINCT FROM OLD.config_snapshot) THEN
            RAISE EXCEPTION 'Forensic Integrity Violation: config_snapshot is immutable after attempt initiation.';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_exam_inmutability ON public.exams;
CREATE TRIGGER trg_exam_inmutability BEFORE UPDATE ON public.exams FOR EACH ROW EXECUTE FUNCTION public.enforce_exam_inmutability();

DROP TRIGGER IF EXISTS trg_snapshot_protection ON public.exam_attempts;
CREATE TRIGGER trg_snapshot_protection BEFORE UPDATE ON public.exam_attempts FOR EACH ROW EXECUTE FUNCTION public.enforce_exam_inmutability();

-- 10. RPCs SEGUROS PARA EL CLIENTE (SECURITY DEFINER)

-- Obtener intento activo
CREATE OR REPLACE FUNCTION public.get_active_attempt_secure(
    p_exam_id UUID,
    p_learner_id UUID
)
RETURNS SETOF public.exam_attempts
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF (auth.uid() != p_learner_id) AND (NOT public.is_admin()) THEN
        RAISE EXCEPTION 'Unauthorized: Identity mismatch.';
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

-- Crear nuevo intento
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
    IF (auth.uid() != p_learner_id) AND (NOT public.is_admin()) THEN
        RAISE EXCEPTION 'Unauthorized: Identity mismatch.';
    END IF;

    RETURN QUERY
    INSERT INTO public.exam_attempts (
        exam_id,
        learner_id,
        status,
        config_snapshot
    ) VALUES (
        p_exam_id,
        p_learner_id,
        'IN_PROGRESS',
        p_config_snapshot
    )
    RETURNING *;
END;
$$ LANGUAGE plpgsql;

-- 11. ÍNDICES DE OPTIMIZACIÓN
CREATE INDEX IF NOT EXISTS idx_tsm_student ON public.teacher_student_mapping (student_id);
CREATE INDEX IF NOT EXISTS idx_cohorts_teacher ON public.cohorts (teacher_id);
CREATE INDEX IF NOT EXISTS idx_cohort_members_student ON public.cohort_members (student_id);
CREATE INDEX IF NOT EXISTS idx_exams_creator_status ON public.exams (creator_id, status);
CREATE INDEX IF NOT EXISTS idx_exam_attempts_exam ON public.exam_attempts (exam_id);
CREATE INDEX IF NOT EXISTS idx_exam_attempts_learner ON public.exam_attempts (learner_id);
CREATE INDEX IF NOT EXISTS idx_telemetry_attempt ON public.telemetry_logs (attempt_id);
CREATE INDEX IF NOT EXISTS idx_submissions_learner ON public.submissions (learner_id);
CREATE INDEX IF NOT EXISTS idx_comp_edges_source ON public.competency_edges (source_id);
CREATE INDEX IF NOT EXISTS idx_comp_edges_target ON public.competency_edges (target_id);
CREATE INDEX IF NOT EXISTS idx_probes_competency ON public.diagnostic_probes (competency_id);
CREATE INDEX IF NOT EXISTS idx_probe_options_probe ON public.probe_options (probe_id);
CREATE INDEX IF NOT EXISTS idx_probe_options_misconception ON public.probe_options (diagnoses_misconception_id);
CREATE INDEX IF NOT EXISTS idx_ai_usage_user ON public.ai_usage_logs (user_id);

-- 12. SECCIÓN RLS & SECURITY POLICIES (Consolidado v5.0)

-- Limpieza de Políticas Existentes para asegurar Idempotencia
DO $$ 
DECLARE 
    pol RECORD;
BEGIN 
    FOR pol IN (SELECT policyname, tablename FROM pg_policies WHERE schemaname = 'public') LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', pol.policyname, pol.tablename);
    END LOOP;
END $$;

-- Habilitar RLS en tablas críticas
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.learners ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.teacher_student_mapping ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cohorts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cohort_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.competency_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.competency_edges ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.diagnostic_probes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.probe_options ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.exams ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.exam_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.exam_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.telemetry_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.submissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.course_syllabus ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.item_bank ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.misconception_feedback ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.draft_exams ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ai_usage_logs ENABLE ROW LEVEL SECURITY;

-- Perfiles
CREATE POLICY "profiles_isolation" ON public.profiles FOR ALL TO authenticated 
USING (auth.uid() = id OR public.is_admin())
WITH CHECK (auth.uid() = id OR public.is_admin());

-- Aislamiento Institucional Estudiantes (M:N)
CREATE POLICY "learners_institutional_isolation" ON public.learners FOR ALL TO authenticated
USING (
    EXISTS(SELECT 1 FROM public.teacher_student_mapping tsm WHERE tsm.student_id = public.learners.id AND tsm.teacher_id = auth.uid())
    OR public.is_admin()
);

-- Mapeos Profesores-Estudiantes
CREATE POLICY "tsm_isolation" ON public.teacher_student_mapping FOR ALL TO authenticated
USING (teacher_id = auth.uid() OR public.is_admin());

-- Cohortes
CREATE POLICY "cohorts_isolation" ON public.cohorts FOR ALL TO authenticated
USING (teacher_id = auth.uid() OR public.is_admin());

CREATE POLICY "cohort_members_isolation" ON public.cohort_members FOR ALL TO authenticated
USING (
    EXISTS (
        SELECT 1 FROM public.cohorts c
        WHERE c.id = public.cohort_members.cohort_id
        AND (c.teacher_id = auth.uid() OR public.is_admin())
    )
);

-- Motor Pedagógico (Lectura Pública, Gestión Staff)
CREATE POLICY "competency_read_access" ON public.competency_nodes FOR SELECT TO authenticated USING (TRUE);
CREATE POLICY "competency_write_shield" ON public.competency_nodes FOR ALL TO authenticated USING (public.is_staff());

CREATE POLICY "edges_read_access" ON public.competency_edges FOR SELECT TO authenticated USING (TRUE);
CREATE POLICY "edges_write_shield" ON public.competency_edges FOR ALL TO authenticated USING (public.is_staff());

CREATE POLICY "probes_read_access" ON public.diagnostic_probes FOR SELECT TO authenticated USING (TRUE);
CREATE POLICY "probes_write_shield" ON public.diagnostic_probes FOR ALL TO authenticated USING (public.is_staff());

CREATE POLICY "probe_options_read_access" ON public.probe_options FOR SELECT TO authenticated USING (TRUE);
CREATE POLICY "probe_options_write_shield" ON public.probe_options FOR ALL TO authenticated USING (public.is_staff());

CREATE POLICY "item_bank_read_access" ON public.item_bank FOR SELECT TO authenticated USING (TRUE);
CREATE POLICY "item_bank_write_shield" ON public.item_bank FOR ALL TO authenticated USING (public.is_staff());

-- Exámenes y Asignaciones
CREATE POLICY "exams_read_access" ON public.exams FOR SELECT TO authenticated
USING (
    creator_id = auth.uid() OR 
    public.is_admin() OR 
    EXISTS(SELECT 1 FROM public.teacher_student_mapping tsm WHERE tsm.teacher_id = public.exams.creator_id AND tsm.student_id = auth.uid())
);

CREATE POLICY "exams_write_shield" ON public.exams FOR ALL TO authenticated
USING (creator_id = auth.uid() AND status = 'DRAFT')
WITH CHECK (creator_id = auth.uid() AND status = 'DRAFT');

CREATE POLICY "assignments_isolation" ON public.exam_assignments FOR ALL TO authenticated
USING (
    student_id = auth.uid() OR 
    EXISTS(SELECT 1 FROM public.exams e WHERE e.id = public.exam_assignments.exam_id AND e.creator_id = auth.uid()) OR
    public.is_admin()
);

-- Intentos y Telemetría (Forense)
CREATE POLICY "attempts_forensic_access" ON public.exam_attempts FOR SELECT TO authenticated
USING (
    learner_id = auth.uid() OR 
    public.is_admin() OR
    (
        public.is_staff() AND
        EXISTS (
            SELECT 1 FROM public.exams e
            JOIN public.teacher_student_mapping tsm ON tsm.teacher_id = e.creator_id
            WHERE e.id = public.exam_attempts.exam_id 
            AND e.creator_id = auth.uid()
            AND tsm.student_id = public.exam_attempts.learner_id
        )
    )
);

CREATE POLICY "attempts_student_update" ON public.exam_attempts FOR UPDATE TO authenticated
USING (learner_id = auth.uid() OR public.is_admin())
WITH CHECK (learner_id = auth.uid() OR public.is_admin());

CREATE POLICY "telemetry_forensic_isolation" ON public.telemetry_logs FOR SELECT TO authenticated
USING (
    EXISTS (
        SELECT 1 FROM public.exam_attempts ea
        WHERE ea.id = telemetry_logs.attempt_id 
        AND (
            ea.learner_id = auth.uid() OR 
            public.is_admin() OR
            (
                public.is_staff() AND
                EXISTS (
                    SELECT 1 FROM public.exams e
                    JOIN public.teacher_student_mapping tsm ON tsm.teacher_id = e.creator_id
                    WHERE e.id = ea.exam_id 
                    AND e.creator_id = auth.uid()
                    AND tsm.student_id = ea.learner_id
                )
            )
        )
    )
);

CREATE POLICY "telemetry_student_insert" ON public.telemetry_logs FOR INSERT TO authenticated
WITH CHECK (
    EXISTS (
        SELECT 1 FROM public.exam_attempts ea
        WHERE ea.id = telemetry_logs.attempt_id
        AND ea.learner_id = auth.uid()
        AND ea.status = 'IN_PROGRESS'
    )
);

-- Syllabus y Feedback
CREATE POLICY "syllabus_read_access" ON public.course_syllabus FOR SELECT TO authenticated
USING (
    EXISTS (SELECT 1 FROM public.courses c WHERE c.id = course_syllabus.course_id AND (c.teacher_id = auth.uid() OR public.is_admin())) OR
    EXISTS (SELECT 1 FROM public.teacher_student_mapping tsm JOIN public.courses c ON c.teacher_id = tsm.teacher_id WHERE c.id = course_syllabus.course_id AND tsm.student_id = auth.uid())
);

CREATE POLICY "syllabus_write_shield" ON public.course_syllabus FOR ALL TO authenticated
USING (EXISTS (SELECT 1 FROM public.courses c WHERE c.id = course_syllabus.course_id AND c.teacher_id = auth.uid()) OR public.is_admin());

CREATE POLICY "misconception_feedback_isolation" ON public.misconception_feedback FOR ALL TO authenticated
USING (public.is_staff())
WITH CHECK (public.is_staff());

-- AI & Discovery
CREATE POLICY "draft_exams_isolation" ON public.draft_exams FOR ALL TO authenticated
USING (public.is_staff())
WITH CHECK (public.is_staff());

CREATE POLICY "ai_usage_logs_admin_only" ON public.ai_usage_logs FOR SELECT TO authenticated
USING (public.is_admin());

-- Anti-Cheat: Ocultar Snapshot de Alumnos
REVOKE SELECT (config_snapshot) ON public.exam_attempts FROM authenticated, anon;
GRANT SELECT (config_snapshot) ON public.exam_attempts TO service_role;
