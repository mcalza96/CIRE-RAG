-- Trigger: update_structural_diagnosis_on_state_change
-- Automatically updates results_cache with the Structural Diagnosis whenever current_state changes.

CREATE OR REPLACE FUNCTION trigger_update_diagnosis()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_diagnosis JSONB;
BEGIN
    -- Only run if current_state has changed
    IF NEW.current_state IS DISTINCT FROM OLD.current_state THEN
        -- Call the RPC to get the diagnosis
        v_diagnosis := get_structural_diagnosis(NEW.id);
        
        -- Update the results_cache with the new diagnosis
        -- We presume results_cache is a JSONB column. We'll merge or overwrite.
        -- Here we overwrite specific keys related to structural diagnosis logic.
        NEW.results_cache := coalesce(NEW.results_cache, '{}'::jsonb) || jsonb_build_object(
            'structural_diagnosis', v_diagnosis,
            'last_updated', now()
        );
    END IF;
    
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_update_diagnosis ON "public"."exam_attempts";

CREATE TRIGGER trg_update_diagnosis
    BEFORE UPDATE ON "public"."exam_attempts"
    FOR EACH ROW
    EXECUTE FUNCTION trigger_update_diagnosis();
