-- ==============================================================================
-- INTELLIGENCE SUITE ANALYTICS VIEWS
-- ==============================================================================

-- 1. VIEW: vw_pathology_ranking (Ranking de Patologías)
-- Aggregates misconception triggers from the results_cache.
DROP VIEW IF EXISTS vw_pathology_ranking CASCADE;

CREATE OR REPLACE VIEW vw_pathology_ranking AS
WITH expanded_diagnoses AS (
    SELECT 
        e.creator_id as teacher_id,
        ea.exam_config_id as exam_id,
        (diag->>'competencyId')::text as competency_id,
        (diag->>'state')::text as state,
        (diag->'evidence'->>'reason')::text as reason,
        (diag->'evidence'->>'confidenceScore')::float as confidence_score,
        (diag->'evidence'->>'hesitationCount')::int as hesitation_count, -- New: Forensic
        (diag->'evidence'->>'timeMs')::int as time_ms -- New: Forensic
    FROM exam_attempts ea
    JOIN exams e ON ea.exam_config_id = e.id
    CROSS JOIN LATERAL jsonb_array_elements(ea.results_cache->'competencyDiagnoses') as diag
    WHERE ea.status = 'COMPLETED'
)
SELECT 
    teacher_id,
    exam_id,
    competency_id,
    state,
    COUNT(*) as total_occurrences,
    AVG(confidence_score) as avg_confidence_score,
    AVG(hesitation_count) as avg_hesitation_index, -- New
    AVG(time_ms) as avg_response_time -- New
FROM expanded_diagnoses
WHERE state = 'MISCONCEPTION'
GROUP BY teacher_id, exam_id, competency_id, state;


-- 2. VIEW: vw_item_health (La Sala de Máquinas)
DROP VIEW IF EXISTS vw_item_health CASCADE;

CREATE OR REPLACE VIEW vw_item_health AS
WITH raw_answers AS (
    SELECT 
        e.creator_id as teacher_id,
        ea.exam_config_id as exam_id,
        key as question_id,
        (value->>'isCorrect')::boolean as is_correct,
        (value->>'timeMs')::int as time_ms,
        (value->>'confidence')::text as confidence
    FROM exam_attempts ea
    JOIN exams e ON ea.exam_config_id = e.id
    CROSS JOIN LATERAL jsonb_each(ea.current_state) as answers(key, value)
    WHERE ea.status = 'COMPLETED'
),
item_stats AS (
    SELECT
        teacher_id,
        exam_id,
        question_id,
        COUNT(*) as total_responses,
        COUNT(CASE WHEN is_correct THEN 1 END) as correct_count,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY time_ms) as median_time_ms
    FROM raw_answers
    GROUP BY teacher_id, exam_id, question_id
)
SELECT
    teacher_id,
    exam_id,
    question_id,
    total_responses,
    (correct_count::float / NULLIF(total_responses, 0)) * 100 as accuracy_rate,
    median_time_ms,
    CASE
        WHEN (correct_count::float / NULLIF(total_responses, 0)) = 0 AND median_time_ms > 60000 THEN 'BROKEN' -- Too hard/long
        WHEN (correct_count::float / NULLIF(total_responses, 0)) = 1 AND median_time_ms < 5000 THEN 'TRIVIAL' -- Too easy/fast
        ELSE 'HEALTHY'
    END as health_status
FROM item_stats;


-- 3. VIEW: vw_cohort_radar (Radar de Alumnos)
DROP VIEW IF EXISTS vw_cohort_radar CASCADE;

CREATE OR REPLACE VIEW vw_cohort_radar AS
SELECT 
    e.creator_id as teacher_id,
    ea.exam_config_id as exam_id,
    ea.learner_id as student_id,
    (ea.results_cache->>'overallScore')::float as overall_score,
    (ea.results_cache->'calibration'->>'eceScore')::float as ece_score,
    (ea.results_cache->'behaviorProfile'->>'isImpulsive')::boolean as is_impulsive,
    (ea.results_cache->'behaviorProfile'->>'isAnxious')::boolean as is_anxious,
    CASE
        WHEN (ea.results_cache->'calibration'->>'eceScore')::float < 10 AND (ea.results_cache->>'overallScore')::float > 80 THEN 'MASTER'
        WHEN (ea.results_cache->'behaviorProfile'->>'isImpulsive')::boolean THEN 'IMPULSIVE'
        WHEN (ea.results_cache->'behaviorProfile'->>'isAnxious')::boolean THEN 'UNCERTAIN'
        WHEN (ea.results_cache->'calibration'->>'eceScore')::float > 20 THEN 'DELUSIONAL'
        ELSE 'DEVELOPING'
    END as student_archetype
FROM exam_attempts ea
JOIN exams e ON ea.exam_config_id = e.id
WHERE ea.status = 'COMPLETED';


-- 4. VIEW: vw_remediation_fairness (Auditoría de Equidad)
DROP VIEW IF EXISTS vw_remediation_fairness CASCADE;

CREATE OR REPLACE VIEW vw_remediation_fairness AS
SELECT 
    e.creator_id as teacher_id,
    ea.exam_config_id as exam_id,
    p.demographic_group,
    COUNT(*) as total_attempts,
    SUM(CASE WHEN (ea.results_cache->>'overallScore')::float < 60 THEN 1 ELSE 0 END) as failed_attempts,
    AVG((ea.results_cache->>'overallScore')::float) as avg_score,
    -- Calculate "Intervention Rate" (Failure Rate)
    (SUM(CASE WHEN (ea.results_cache->>'overallScore')::float < 60 THEN 1 ELSE 0 END)::float / COUNT(*)) as intervention_rate
FROM exam_attempts ea
JOIN exams e ON ea.exam_config_id = e.id
JOIN profiles p ON ea.learner_id = p.id
WHERE ea.status = 'COMPLETED'
GROUP BY e.creator_id, ea.exam_config_id, p.demographic_group;
