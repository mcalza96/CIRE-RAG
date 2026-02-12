-- Migration: Security Hardening (UBB PILOT)
-- Description: Refines RLS for attempts and telemetry, secures instructor access, and ensures exam inmutability.

-- 1. Helper for Staff Role (Admin or Instructor)
CREATE OR REPLACE FUNCTION public.is_staff()
RETURNS BOOLEAN AS $$
BEGIN
  RETURN (
    SELECT (role IN ('admin', 'instructor', 'teacher'))
    FROM public.profiles
    WHERE id = auth.uid()
  );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 2. exam_attempts: Strategic Isolation
-- Learners see their own, Instructors see their own exams' results, Admins see all.
ALTER TABLE public.exam_attempts ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Learners can view their own attempts" ON public.exam_attempts;
DROP POLICY IF EXISTS "Staff can view attempts of their own exams" ON public.exam_attempts;
DROP POLICY IF EXISTS "Combined access for exam_attempts" ON public.exam_attempts;

CREATE POLICY "Combined access for exam_attempts"
ON public.exam_attempts FOR SELECT
TO authenticated
USING (
    learner_id = auth.uid() OR 
    public.is_admin() OR
    (public.is_staff() AND EXISTS (
        SELECT 1 FROM public.exams 
        WHERE public.exams.id = exam_attempts.exam_config_id 
        AND public.exams.creator_id = auth.uid()
    ))
);

-- 3. telemetry_logs: Advanced Forensic Isolation
ALTER TABLE public.telemetry_logs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Learners can view telemetry_logs of their own attempts" ON public.telemetry_logs;
DROP POLICY IF EXISTS "Combined access for telemetry_logs" ON public.telemetry_logs;

CREATE POLICY "Combined access for telemetry_logs"
ON public.telemetry_logs FOR SELECT
TO authenticated
USING (
    EXISTS (
        SELECT 1 FROM public.exam_attempts 
        WHERE public.exam_attempts.id = telemetry_logs.attempt_id 
        AND (
            public.exam_attempts.learner_id = auth.uid() OR 
            public.is_admin() OR
            (public.is_staff() AND EXISTS (
                SELECT 1 FROM public.exams 
                WHERE public.exams.id = public.exam_attempts.exam_config_id 
                AND public.exams.creator_id = auth.uid()
            ))
        )
    )
);

-- 4. Inmutability: Protect Published Exams
-- This prevents accidental tampering with a live diagnostic instrument.
CREATE OR REPLACE FUNCTION public.enforce_exam_inmutability()
RETURNS TRIGGER AS $$
BEGIN
    -- If the exam is already published, we block any changes to the questions or config
    IF OLD.status = 'PUBLISHED' AND NEW.status = 'PUBLISHED' THEN
        IF (NEW.config_json IS DISTINCT FROM OLD.config_json OR NEW.questions IS DISTINCT FROM OLD.questions) THEN
            RAISE EXCEPTION 'Diagnostic integrity violation: Cannot modify a PUBLISHED exam. Versioning required.';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_exam_inmutability ON public.exams;
CREATE TRIGGER trg_exam_inmutability
BEFORE UPDATE ON public.exams
FOR EACH ROW
EXECUTE FUNCTION public.enforce_exam_inmutability();

-- 5. Harden telemetry_logs insertion
DROP POLICY IF EXISTS "Learners can insert telemetry_logs for their own attempts" ON public.telemetry_logs;
CREATE POLICY "Learners can insert telemetry_logs for their own attempts"
ON public.telemetry_logs FOR INSERT
TO authenticated
WITH CHECK (
    EXISTS (
        SELECT 1 FROM public.exam_attempts 
        WHERE public.exam_attempts.id = attempt_id 
        AND public.exam_attempts.learner_id = auth.uid()
        AND public.exam_attempts.status = 'IN_PROGRESS'
    )
);
