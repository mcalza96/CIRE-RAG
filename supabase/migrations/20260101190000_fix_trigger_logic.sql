-- Migration: Fix Shared Trigger Logic (Table Guards)
-- Description: Restores the TG_TABLE_NAME guards to prevent enum casting errors 
--              when the trigger runs on different tables (exam vs exam_attempts).

CREATE OR REPLACE FUNCTION public.enforce_exam_inmutability()
RETURNS TRIGGER AS $$
BEGIN
    -- 1. Logic for 'exams' table: Protect PUBLISHED configs
    IF TG_TABLE_NAME = 'exams' THEN
        -- We handle both cases if the status is enum or text
        IF (OLD.status::text = 'PUBLISHED') AND (NEW.status::text = 'PUBLISHED') THEN
            IF (NEW.config_json IS DISTINCT FROM OLD.config_json OR NEW.questions IS DISTINCT FROM OLD.questions) THEN
                RAISE EXCEPTION 'Diagnostic integrity violation: Cannot modify a PUBLISHED exam. Versioning required.';
            END IF;
        END IF;
    END IF;

    -- 2. Logic for 'exam_attempts' table: Snapshot Protection
    IF TG_TABLE_NAME = 'exam_attempts' THEN
        -- Once created, config_snapshot must never change
        IF (NEW.config_snapshot IS DISTINCT FROM OLD.config_snapshot) THEN
            RAISE EXCEPTION 'Diagnostic integrity violation: Cannot modify config_snapshot after attempt initiation.';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- No need to recreate triggers as they already point to this function name.
