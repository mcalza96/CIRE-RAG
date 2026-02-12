-- Migration: Secure Attempt Management RPCs (Service Role Bypass)
-- Description: Allows authenticated students to access their own exam snapshots securely via RPC, 
--              bypassing the column-level REVOKE on config_snapshot without needing a Service Role Key.

-- 1. Secure Getter
CREATE OR REPLACE FUNCTION public.get_active_attempt_secure(
    p_exam_config_id UUID,
    p_learner_id UUID
)
RETURNS SETOF public.exam_attempts
SECURITY DEFINER -- Runs with privileges of the creator (postgres/admin)
SET search_path = public
AS $$
BEGIN
    -- Authorization Check: Caller must be the learner OR Admin
    IF (auth.uid() != p_learner_id) AND (NOT public.is_admin()) THEN
        RAISE EXCEPTION 'Unauthorized: Identity mismatch in secure attempt retrieval.';
    END IF;

    RETURN QUERY
    SELECT *
    FROM public.exam_attempts
    WHERE exam_config_id = p_exam_config_id
      AND learner_id = p_learner_id
      AND status = 'IN_PROGRESS'
    LIMIT 1;
END;
$$ LANGUAGE plpgsql;

-- 2. Secure Creator
CREATE OR REPLACE FUNCTION public.create_exam_attempt_secure(
    p_exam_config_id UUID,
    p_learner_id UUID,
    p_config_snapshot JSONB
)
RETURNS SETOF public.exam_attempts
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    -- Authorization Check
    IF (auth.uid() != p_learner_id) AND (NOT public.is_admin()) THEN
        RAISE EXCEPTION 'Unauthorized: Identity mismatch in secure attempt creation.';
    END IF;

    -- Integrity Check: Verify exam exists and is PUBLISHED (optional but good practice)
    -- We skip strict status check here to allow admins to test drafts, 
    -- but usually we'd enforce it. Relying on foreign key constraints for existence.

    RETURN QUERY
    INSERT INTO public.exam_attempts (
        exam_config_id,
        learner_id,
        status,
        current_state,
        config_snapshot
    ) VALUES (
        p_exam_config_id,
        p_learner_id,
        'IN_PROGRESS',
        '{}'::jsonb,
        p_config_snapshot
    )
    RETURNING *;
END;
$$ LANGUAGE plpgsql;

REVOKE EXECUTE ON FUNCTION public.get_active_attempt_secure FROM public;
GRANT EXECUTE ON FUNCTION public.get_active_attempt_secure TO authenticated;

REVOKE EXECUTE ON FUNCTION public.create_exam_attempt_secure FROM public;
GRANT EXECUTE ON FUNCTION public.create_exam_attempt_secure TO authenticated;
