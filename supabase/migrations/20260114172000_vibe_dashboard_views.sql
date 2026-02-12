-- Migration: Create SQL Views for Vibe Dashboard Analytics

-- 1. vw_pathology_ranking
-- Aggregates diagnosed misconceptions by count to identify "Cohort Pathologies".
DROP VIEW IF EXISTS vw_pathology_ranking CASCADE;

CREATE OR REPLACE VIEW vw_pathology_ranking AS
SELECT 
    ea.exam_id,
    po.diagnoses_misconception_id AS misconception_id,
    COUNT(*) AS frequency,
    MAX(ea.last_active_at) as last_detected_at
FROM exam_attempts ea
CROSS JOIN LATERAL jsonb_each_text(ea.current_state) as answers(question_id, option_id)
JOIN probe_options po ON po.id = answers.option_id::UUID
WHERE 
    po.is_correct = false 
    AND po.diagnoses_misconception_id IS NOT NULL
GROUP BY ea.exam_id, po.diagnoses_misconception_id
ORDER BY frequency DESC;

-- 2. vw_item_health
-- Aggregates item performance including Rapid Guessing and Fail Rate.
DROP VIEW IF EXISTS vw_item_health CASCADE;

CREATE OR REPLACE VIEW vw_item_health AS
WITH ItemStats AS (
    SELECT
        CAST(key as UUID) as question_id,
        ea.exam_id,
        COUNT(*) as total_attempts,
        SUM(CASE WHEN po.is_correct THEN 1 ELSE 0 END) as correct_count,
        SUM(CASE WHEN po.is_correct THEN 0 ELSE 1 END) as incorrect_count,
        -- We would need telemetry_logs specifically for rapid guessing aggregation if not cached in attempt
        0 as rapid_guess_count -- Placeholder until telemetry integration in view
    FROM exam_attempts ea
    CROSS JOIN LATERAL jsonb_each_text(ea.current_state) as answers(key, value)
    JOIN probe_options po ON po.id = answers.value::UUID
    GROUP BY key, ea.exam_id
)
SELECT
    question_id,
    exam_id,
    total_attempts,
    (incorrect_count::float / NULLIF(total_attempts, 0)) * 100 as fail_rate_percentage,
    CASE 
        WHEN (incorrect_count::float / NULLIF(total_attempts, 0)) > 0.8 THEN 'TOO_HARD'
        WHEN (incorrect_count::float / NULLIF(total_attempts, 0)) < 0.1 THEN 'TOO_EASY'
        ELSE 'BALANCED'
    END as health_status
FROM ItemStats;
