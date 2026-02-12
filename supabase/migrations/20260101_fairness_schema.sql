-- 1. Enriquecer Perfil de Estudiante con Metadata de Equidad
ALTER TABLE public.profiles
ADD COLUMN IF NOT EXISTS demographic_group TEXT DEFAULT 'generic', -- e.g. 'Group A', 'Group B'
ADD COLUMN IF NOT EXISTS primary_language TEXT DEFAULT 'es',
ADD COLUMN IF NOT EXISTS access_type TEXT DEFAULT 'desktop'; -- 'mobile', 'desktop', 'mixed'

-- 2. Crear Vista de Paridad de Remediación
DROP VIEW IF EXISTS vw_remediation_fairness CASCADE;

CREATE OR REPLACE VIEW vw_remediation_fairness AS
SELECT 
    e.creator_id as teacher_id,
    p.demographic_group,
    p.access_type,
    COUNT(DISTINCT ea.id) as total_attempts,
    -- Failed attempts (score < 60)
    COUNT(CASE WHEN (ea.results_cache->>'overallScore')::float < 60 THEN 1 END) as failed_attempts,
    -- Avg Score
    AVG((ea.results_cache->>'overallScore')::float) as avg_score,
    -- Intervention Rate (Refutations / Total Attempts)
    -- We assume 'applied_mutations' tracks AI interventions
    ROUND(
        COUNT(CASE WHEN m.value->>'action' = 'INSERT_NODE' THEN 1 END)::numeric / 
        GREATEST(COUNT(DISTINCT ea.id), 1), 2
    )::float as intervention_rate
FROM 
    public.exam_attempts ea
JOIN 
    public.exams e ON ea.exam_id = e.id
JOIN 
    public.profiles p ON ea.learner_id = p.id
LEFT JOIN 
    jsonb_array_elements(ea.applied_mutations) m ON true
WHERE 
    ea.status = 'COMPLETED'
GROUP BY 
    e.creator_id, p.demographic_group, p.access_type;

-- 3. Diferencial Item Functioning (DIF) View
-- Detecta si un ítem tiene tasas de éxito disparmente diferentes entre grupos o dispositivos
DROP VIEW IF EXISTS vw_item_dif CASCADE;

CREATE OR REPLACE VIEW vw_item_dif AS
WITH item_performance AS (
    -- Performance por Grupo Demográfico
    SELECT 
        e.creator_id as teacher_id,
        key as question_id,
        'demographic' as dimension,
        p.demographic_group as sub_group,
        AVG(CASE WHEN (value->>'isCorrect')::boolean THEN 1 ELSE 0 END) as success_rate,
        COUNT(*) as total_responses
    FROM exam_attempts ea
    JOIN exams e ON ea.exam_id = e.id
    JOIN profiles p ON ea.learner_id = p.id
    CROSS JOIN LATERAL jsonb_each(ea.current_state) as answers(key, value)
    WHERE ea.status = 'COMPLETED'
    GROUP BY e.creator_id, key, p.demographic_group
    
    UNION ALL
    
    -- Performance por Tipo de Acceso (Device Neutrality)
    SELECT 
        e.creator_id as teacher_id,
        key as question_id,
        'access_type' as dimension,
        p.access_type as sub_group,
        AVG(CASE WHEN (value->>'isCorrect')::boolean THEN 1 ELSE 0 END) as success_rate,
        COUNT(*) as total_responses
    FROM exam_attempts ea
    JOIN exams e ON ea.exam_id = e.id
    JOIN profiles p ON ea.learner_id = p.id
    CROSS JOIN LATERAL jsonb_each(ea.current_state) as answers(key, value)
    WHERE ea.status = 'COMPLETED'
    GROUP BY e.creator_id, key, p.access_type
)
SELECT 
    question_id,
    dimension,
    MAX(success_rate) - MIN(success_rate) as gap,
    COUNT(DISTINCT sub_group) as compared_groups,
    SUM(total_responses) as total_responses,
    CASE 
        WHEN MAX(success_rate) - MIN(success_rate) > 0.2 THEN 'CRITICAL'
        WHEN MAX(success_rate) - MIN(success_rate) > 0.1 THEN 'WARNING'
        ELSE 'OPTIMAL'
    END as status
FROM item_performance
GROUP BY question_id, dimension
HAVING MAX(success_rate) - MIN(success_rate) > 0.1 AND SUM(total_responses) >= 5;

-- 4. Políticas de Seguridad (RLS) para proteger datos demográficos
-- Solo el usuario puede ver su propia demografía, o un admin.
-- (Asumiendo que profiles ya tiene RLS, solo reforzamos si es necesario o si demographic_group es sensible)
-- En general, las policies de profiles existentes cubren SELECT para el dueño y admin.
