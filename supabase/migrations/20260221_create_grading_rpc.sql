-- ============================================================================
-- RPC: submit_manual_evaluation_transaction
-- Purpose: Atomically save manual assessment and criteria grades.
-- ============================================================================


CREATE OR REPLACE FUNCTION submit_manual_evaluation_transaction(
    p_assessment_id UUID,
    p_final_score FLOAT,
    p_feedback_summary TEXT,
    p_criteria_grades JSONB
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    grade_record JSONB;
BEGIN
    -- 1. Update the main assessment record
    UPDATE public.manual_assessments
    SET 
        final_score = p_final_score,
        feedback_summary = p_feedback_summary,
        status = 'completed',
        updated_at = now()
    WHERE id = p_assessment_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Assessment with ID % not found', p_assessment_id;
    END IF;

    -- 2. Upsert criteria grades (Atomic Batch)
    -- We assume p_criteria_grades is an array of objects:
    -- { "criterion_id": "...", "score_obtained": 10, "feedback_comment": "..." }
    
    FOR grade_record IN SELECT * FROM jsonb_array_elements(p_criteria_grades)
    LOOP
        INSERT INTO public.manual_criteria_grades (
            assessment_id,
            criterion_id,
            score_obtained,
            feedback_comment
        )
        VALUES (
            p_assessment_id,
            (grade_record->>'criterion_id')::UUID,
            (grade_record->>'score_obtained')::FLOAT,
            grade_record->>'feedback_comment'
        )
        ON CONFLICT (assessment_id, criterion_id)
        DO UPDATE SET
            score_obtained = EXCLUDED.score_obtained,
            feedback_comment = EXCLUDED.feedback_comment,
            created_at = now(); -- refresh time
    END LOOP;

END;
$$;
