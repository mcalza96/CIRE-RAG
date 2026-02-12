
-- TTabla para historial de calibración de ítems (Slip/Guess parameters)
CREATE TABLE item_calibration_history (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    exam_id UUID REFERENCES exams(id) ON DELETE CASCADE,
    question_id UUID NOT NULL,
    cohort_id UUID, -- Opcional, para versionar por ciclo temporal
    slip_param NUMERIC(4,3), -- Probabilidad de fallo siendo experto (s_j)
    guess_param NUMERIC(4,3), -- Probabilidad de acierto siendo novato (g_j)
    difficulty_index NUMERIC(4,3), -- p-value (tasa de acierto cruda)
    discrimination_index NUMERIC(4,3), -- Point-Biserial correlation
    calibration_date TIMESTAMPTZ DEFAULT NOW(),
    
    CONSTRAINT check_params_range CHECK (
        slip_param BETWEEN 0 AND 1 AND 
        guess_param BETWEEN 0 AND 1
    )
);

CREATE INDEX idx_calibration_question ON item_calibration_history(question_id);
CREATE INDEX idx_calibration_exam ON item_calibration_history(exam_id);

-- Tabla para alertas de integridad pedagógica (Concept Drift, Ambiguity, etc.)
CREATE TABLE integrity_alerts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    teacher_id UUID REFERENCES auth.users(id) ON DELETE CASCADE, -- Tenant Isolation
    exam_id UUID REFERENCES exams(id) ON DELETE CASCADE,
    question_id UUID, -- Puede ser NULL si la alerta es a nivel de examen
    competency_id UUID, -- Para Concept Drift
    alert_type TEXT CHECK (alert_type IN ('CONCEPT_DRIFT', 'HIGH_SLIP', 'USELESS_DISTRACTOR', 'FRAGILE_PREREQUISITE')),
    severity TEXT CHECK (severity IN ('LOW', 'MEDIUM', 'CRITICAL')),
    message TEXT NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb, -- Datos extra: slip_value, selection_rate, etc.
    is_resolved BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_alerts_teacher ON integrity_alerts(teacher_id);
CREATE INDEX idx_alerts_exam ON integrity_alerts(exam_id);

-- RPC to fetch combined health and calibration data
CREATE OR REPLACE FUNCTION get_item_calibration_snapshot()
RETURNS TABLE (
    question_id UUID,
    exam_id UUID,
    teacher_id UUID,
    exam_title TEXT,
    total_responses BIGINT,
    accuracy_rate FLOAT,
    median_time_ms FLOAT,
    health_status TEXT,
    slip_param NUMERIC(4,3),
    guess_param NUMERIC(4,3)
) AS $$
BEGIN
    RETURN QUERY
    WITH latest_calibration AS (
        SELECT DISTINCT ON (ich.question_id)
            ich.question_id,
            ich.slip_param,
            ich.guess_param
        FROM item_calibration_history ich
        ORDER BY ich.question_id, ich.calibration_date DESC
    )
    SELECT 
        vh.question_id::UUID,
        vh.exam_id,
        vh.teacher_id,
        e.title as exam_title,
        vh.total_responses,
        vh.accuracy_rate,
        vh.median_time_ms,
        vh.health_status,
        lc.slip_param,
        lc.guess_param
    FROM vw_item_health vh
    JOIN exams e ON vh.exam_id = e.id
    LEFT JOIN latest_calibration lc ON vh.question_id::UUID = lc.question_id
    ORDER BY vh.accuracy_rate ASC;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
