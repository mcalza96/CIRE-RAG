-- Migration: Add config_snapshot to exam_attempts and enforce inmutability
-- Description: Captures the exam configuration at the start of an attempt for forensic auditability.

-- 1. Add config_snapshot column
-- We allow NULL initially to avoid breaking existing records, but we'll enforce NOT NULL for new ones logically in code 
-- and can add a constraint later if needed. However, the requirement says "NOT NULL para nuevos registros".
ALTER TABLE public.exam_attempts 
ADD COLUMN IF NOT EXISTS config_snapshot JSONB;

-- Note: In a real production DB, we'd migrates old rows or set a default.
-- For this pilot, we'll set a default of '{}' for existing rows to allow NOT NULL.
UPDATE public.exam_attempts SET config_snapshot = '{}'::jsonb WHERE config_snapshot IS NULL;

ALTER TABLE public.exam_attempts 
ALTER COLUMN config_snapshot SET NOT NULL;

-- 2. Update the trigger function to handle snapshot inmutability
-- We use the existing function name mentioned in requirements: enforce_exam_inmutability
CREATE OR REPLACE FUNCTION public.enforce_exam_inmutability()
RETURNS TRIGGER AS $$
BEGIN
    -- Logic for 'exams' table (Existing)
    IF TG_TABLE_NAME = 'exams' THEN
        IF OLD.status = 'PUBLISHED' AND NEW.status = 'PUBLISHED' THEN
            IF (NEW.config_json IS DISTINCT FROM OLD.config_json OR NEW.questions IS DISTINCT FROM OLD.questions) THEN
                RAISE EXCEPTION 'Diagnostic integrity violation: Cannot modify a PUBLISHED exam. Versioning required.';
            END IF;
        END IF;
    END IF;

    -- Logic for 'exam_attempts' table (New Snapshot Protection)
    IF TG_TABLE_NAME = 'exam_attempts' THEN
        -- Once created, config_snapshot must never change
        IF (NEW.config_snapshot IS DISTINCT FROM OLD.config_snapshot) THEN
            RAISE EXCEPTION 'Diagnostic integrity violation: Cannot modify config_snapshot after attempt initiation.';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 3. Attach the trigger to exam_attempts
DROP TRIGGER IF EXISTS trg_exam_attempt_snapshot_inmutability ON public.exam_attempts;
CREATE TRIGGER trg_exam_attempt_snapshot_inmutability
BEFORE UPDATE ON public.exam_attempts
FOR EACH ROW
EXECUTE FUNCTION public.enforce_exam_inmutability();

-- 4. RLS & Permissions Update: Ensure snapshot is strictly for Server-Side processing
-- We revoke select on the sensitive column from public/authenticated users.
-- This prevents a technical student from viewing answers/matriz Q via DevTools.
REVOKE SELECT (config_snapshot) ON public.exam_attempts FROM authenticated, anon;
-- Staff/Admin can still see it if they have appropriate roles, 
-- but we generally prefer using a service_role for evaluation.
