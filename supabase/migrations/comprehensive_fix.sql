-- =============================================================================
-- MIGRACIÓN: COMPREHENSIVE SECURITY FIX
-- Descripción: Limpia y reaplica TODA la lógica de seguridad y RLS de CISRE.
-- =============================================================================

BEGIN;

-- 1. LIMPIEZA DE POLÍTICAS EXISTENTES (Evitar duplicados)
DO $$ 
DECLARE 
    pol RECORD;
BEGIN 
    FOR pol IN (SELECT policyname, tablename FROM pg_policies WHERE schemaname = 'public') LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', pol.policyname, pol.tablename);
    END LOOP;
END $$;

-- 2. NORMALIZACIÓN DE NOMENCLATURA (Resiliencia Forense)
DO $$
BEGIN
    -- exam_config_id -> exam_id
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'exam_attempts' AND column_name = 'exam_config_id') THEN
        ALTER TABLE public.exam_attempts RENAME COLUMN exam_config_id TO exam_id;
    END IF;

    -- Garantizar existencia de config_snapshot
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'exam_attempts' AND column_name = 'config_snapshot') THEN
        ALTER TABLE public.exam_attempts ADD COLUMN config_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb;
    END IF;

    -- Garantizar existencia de current_state
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'exam_attempts' AND column_name = 'current_state') THEN
        ALTER TABLE public.exam_attempts ADD COLUMN current_state JSONB NOT NULL DEFAULT '{}'::jsonb;
    END IF;

    -- Garantizar existencia de results_cache
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'exam_attempts' AND column_name = 'results_cache') THEN
        ALTER TABLE public.exam_attempts ADD COLUMN results_cache JSONB DEFAULT '{}'::jsonb;
    END IF;

    -- Garantizar existencia de applied_mutations
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'exam_attempts' AND column_name = 'applied_mutations') THEN
        ALTER TABLE public.exam_attempts ADD COLUMN applied_mutations JSONB DEFAULT '[]'::jsonb;
    END IF;

    -- Garantizar existencia de last_active_at
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'exam_attempts' AND column_name = 'last_active_at') THEN
        ALTER TABLE public.exam_attempts ADD COLUMN last_active_at TIMESTAMPTZ DEFAULT NOW();
    END IF;
END $$;

-- 3. RE-BUILD DE HELPERS DE SEGURIDAD
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

-- 3. RLS & SECURITY POLICIES (Consolidado)

-- PROFILES
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
CREATE POLICY "profiles_isolation" ON public.profiles FOR ALL TO authenticated 
USING (auth.uid() = id OR public.is_admin())
WITH CHECK (auth.uid() = id OR public.is_admin());

-- LEARNERS
ALTER TABLE public.learners ENABLE ROW LEVEL SECURITY;
CREATE POLICY "learners_institutional_isolation" ON public.learners FOR ALL TO authenticated
USING (
    EXISTS(SELECT 1 FROM public.teacher_student_mapping tsm WHERE tsm.student_id = public.learners.id AND tsm.teacher_id = auth.uid())
    OR public.is_admin()
);

-- EXAMS
ALTER TABLE public.exams ENABLE ROW LEVEL SECURITY;
CREATE POLICY "exams_read_access" ON public.exams FOR SELECT TO authenticated
USING (
    creator_id = auth.uid() OR 
    public.is_admin() OR 
    EXISTS(SELECT 1 FROM public.teacher_student_mapping tsm WHERE tsm.teacher_id = public.exams.creator_id AND tsm.student_id = auth.uid())
);
CREATE POLICY "exams_write_autonomy" ON public.exams FOR ALL TO authenticated
USING (creator_id = auth.uid() AND status = 'DRAFT')
WITH CHECK (creator_id = auth.uid() AND status = 'DRAFT');

-- EXAM_ATTEMPTS
ALTER TABLE public.exam_attempts ENABLE ROW LEVEL SECURITY;
CREATE POLICY "attempts_forensic_access" ON public.exam_attempts FOR SELECT TO authenticated
USING (
    learner_id = auth.uid() OR 
    public.is_admin() OR
    EXISTS(SELECT 1 FROM public.exams WHERE public.exams.id = exam_attempts.exam_id AND public.exams.creator_id = auth.uid())
);

-- TELEMETRY_LOGS
ALTER TABLE public.telemetry_logs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "telemetry_forensic_isolation" ON public.telemetry_logs FOR SELECT TO authenticated
USING (
    EXISTS (
        SELECT 1 FROM public.exam_attempts 
        WHERE public.exam_attempts.id = telemetry_logs.attempt_id 
        AND (
            public.exam_attempts.learner_id = auth.uid() OR 
            public.is_admin() OR
            EXISTS (SELECT 1 FROM public.exams WHERE public.exams.id = public.exam_attempts.exam_id AND public.exams.creator_id = auth.uid())
        )
    )
);

-- SUBMISSIONS (PORTFOLIO)
ALTER TABLE public.submissions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "submissions_institutional_isolation" ON public.submissions FOR ALL TO authenticated
USING (
    EXISTS (
        SELECT 1 FROM public.teacher_student_mapping tsm 
        WHERE tsm.student_id = public.submissions.learner_id 
        AND tsm.teacher_id = auth.uid()
    ) OR public.is_admin()
);

-- DRAFT_EXAMS
CREATE TABLE IF NOT EXISTS public.draft_exams (
    lesson_id TEXT PRIMARY KEY,
    context JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE public.draft_exams ENABLE ROW LEVEL SECURITY;
CREATE POLICY "draft_exams_isolation" ON public.draft_exams FOR ALL TO authenticated
USING (true)
WITH CHECK (true);

-- AI_USAGE_LOGS
ALTER TABLE public.ai_usage_logs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "ai_usage_logs_admin_only" ON public.ai_usage_logs FOR SELECT TO authenticated
USING (public.is_admin());

-- 4. UPDATE RPCS FOR NOMENCLATURA CONSISTENCY

-- 4.1 Secure Getter
DROP FUNCTION IF EXISTS public.get_active_attempt_secure(UUID, UUID);
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

-- 4.2 Secure Creator
DROP FUNCTION IF EXISTS public.create_exam_attempt_secure(UUID, UUID, JSONB);
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

-- 5. TRIGGERS DE INMUTABILIDAD FORENSE
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
            RAISE EXCEPTION 'Forensic Integrity Violation: config_snapshot is immutable.';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_exam_inmutability ON public.exams;
CREATE TRIGGER trg_exam_inmutability BEFORE UPDATE ON public.exams FOR EACH ROW EXECUTE FUNCTION public.enforce_exam_inmutability();

DROP TRIGGER IF EXISTS trg_snapshot_protection ON public.exam_attempts;
CREATE TRIGGER trg_snapshot_protection BEFORE UPDATE ON public.exam_attempts FOR EACH ROW EXECUTE FUNCTION public.enforce_exam_inmutability();

-- 6. PROTECCIÓN DE SNAPSHOT (Anti-cheat)
REVOKE SELECT (config_snapshot) ON public.exam_attempts FROM authenticated, anon;
GRANT SELECT (config_snapshot) ON public.exam_attempts TO service_role; 

COMMIT;
