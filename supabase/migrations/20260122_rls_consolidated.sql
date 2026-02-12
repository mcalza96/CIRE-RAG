-- =============================================================================
-- MIGRACIÓN: RLS CONSOLIDATED (v1.0)
-- Descripción: Unifica y robustece toda la capa de seguridad de CISRE.
-- Fecha: 2026-01-22
-- =============================================================================

BEGIN;

-- 1. LIMPIEZA GLOBAL DE POLÍTICAS (Tabula Rasa)
DO $$ 
DECLARE 
    pol RECORD;
BEGIN 
    FOR pol IN (SELECT policyname, tablename FROM pg_policies WHERE schemaname = 'public') LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', pol.policyname, pol.tablename);
    END LOOP;
END $$;

-- 2. HELPERS DE SEGURIDAD (Security Definer)
-- Consolidamos las funciones para evitar fragmentación.

CREATE OR REPLACE FUNCTION public.role_is(required_role public.app_role)
RETURNS BOOLEAN AS $$
BEGIN
  RETURN (
    SELECT (role = required_role)
    FROM public.profiles
    WHERE id = auth.uid()
  );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

CREATE OR REPLACE FUNCTION public.is_admin()
RETURNS BOOLEAN AS $$
BEGIN
  RETURN public.role_is('admin');
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

-- 3. HABILITAR RLS EN TODAS LAS TABLAS NÚCLEO
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

-- 4. POLÍTICAS DE IDENTIDAD Y MULTITENANT

-- 4.1 Perfiles
CREATE POLICY "profiles_isolation" ON public.profiles FOR ALL TO authenticated 
USING (auth.uid() = id OR public.is_admin())
WITH CHECK (auth.uid() = id OR public.is_admin());

-- 4.2 Aislamiento Institucional Estudiantes (M:N)
CREATE POLICY "learners_institutional_isolation" ON public.learners FOR ALL TO authenticated
USING (
    EXISTS(SELECT 1 FROM public.teacher_student_mapping tsm WHERE tsm.student_id = public.learners.id AND tsm.teacher_id = auth.uid())
    OR public.is_admin()
);

-- 4.3 Mapeos Profesores-Estudiantes
CREATE POLICY "tsm_isolation" ON public.teacher_student_mapping FOR ALL TO authenticated
USING (teacher_id = auth.uid() OR public.is_admin());

-- 4.4 Cohortes
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

-- 5. MOTOR PEDAGÓGICO (Lectura Pública, Gestión Staff)
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

-- 6. EXÁMENES Y ASIGNACIONES
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

-- 7. INTENTOS Y TELEMETRÍA (Forense)
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

-- 8. VISTAS Y TABLAS DE APOYO
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

CREATE POLICY "draft_exams_isolation" ON public.draft_exams FOR ALL TO authenticated
USING (public.is_staff())
WITH CHECK (public.is_staff());

CREATE POLICY "ai_usage_logs_admin_only" ON public.ai_usage_logs FOR SELECT TO authenticated
USING (public.is_admin());

-- 9. HARDENING (Anti-cheat)
-- Revocar acceso a campos críticos para el alumno en curso
REVOKE SELECT (config_snapshot) ON public.exam_attempts FROM authenticated, anon;
GRANT SELECT (config_snapshot) ON public.exam_attempts TO service_role;

-- 10. PROTECCIÓN DE MODIFICACIONES CONCURRENTES
-- Asegurar que results_cache no sea modificado por el estudiante directamente
-- (Esto se refuerza con el trigger enforce_exam_inmutability que ya existe)

COMMIT;
