-- RPC: get_structural_diagnosis
-- Calculates the "Pathology of Errors" by joining user answers with hardwired misconceptions.

CREATE OR REPLACE FUNCTION get_structural_diagnosis(p_attempt_id UUID)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_diagnosis JSONB;
BEGIN
    WITH raw_diagnosis AS (
        SELECT 
            ea.learner_id,
            po.diagnoses_misconception_id,
            count(*) as error_count
        FROM exam_attempts ea
        CROSS JOIN LATERAL jsonb_each_text(ea.current_state) as answers(question_id, option_id)
        JOIN probe_options po ON po.id = answers.option_id::UUID
        WHERE ea.id = p_attempt_id
          AND po.is_correct = false
          AND po.diagnoses_misconception_id IS NOT NULL
        GROUP BY ea.learner_id, po.diagnoses_misconception_id
    ),
    
    total_answers AS (
        SELECT count(*) as total
        FROM exam_attempts ea
        CROSS JOIN LATERAL jsonb_each_text(ea.current_state)
        WHERE ea.id = p_attempt_id
    ),
    
    infected_nodes AS (
        SELECT 
            jsonb_agg(DISTINCT diagnoses_misconception_id) as nodes
        FROM raw_diagnosis
    ),
    
    patheology_stats AS (
        SELECT 
            COALESCE(sum(error_count), 0) as total_structural_errors
        FROM raw_diagnosis
    )
    
    SELECT jsonb_build_object(
        'attempt_id', p_attempt_id,
        'total_answers', (SELECT total FROM total_answers),
        'structural_error_count', (SELECT total_structural_errors FROM patheology_stats),
        'infected_nodes', (SELECT nodes FROM infected_nodes),
        'diagnosis_timestamp', now()
    ) INTO v_diagnosis;

    RETURN v_diagnosis;
END;
$$;
