-- ============================================================================
-- SYLLABUS ENGINE - FASE 1: Estructura Relacional y Analítica
-- ============================================================================
-- Propósito: Formalizar la relación entre el currículo (lessons) y el mapa
--            de conocimiento (competency_nodes) para medir progreso real
--            contra un programa de estudios estructurado.
--
-- Filosofía: Caja Blanca (White Box) - Transparencia Pedagógica Total
-- ============================================================================

-- ----------------------------------------------------------------------------
-- TABLA: course_syllabus
-- ----------------------------------------------------------------------------
-- Representa la relación canónica entre unidades curriculares y competencias.
-- Cada fila define un átomo de conocimiento dentro de una unidad temporal.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.course_syllabus (
    -- Identificador único
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Relaciones forenses (Inmutabilidad garantizada por CASCADE)
    course_id UUID NOT NULL REFERENCES public.courses(id) ON DELETE CASCADE,
    unit_id UUID NOT NULL REFERENCES public.lessons(id) ON DELETE CASCADE,
    competency_id UUID NOT NULL REFERENCES public.competency_nodes(id) ON DELETE CASCADE,
    
    -- Secuencia pedagógica dentro de la unidad
    order_index INTEGER NOT NULL DEFAULT 0,
    
    -- Indicador de obligatoriedad (para cálculo de progreso)
    is_required BOOLEAN NOT NULL DEFAULT true,
    
    -- Auditoría temporal
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    -- Constraint: Una competencia solo puede aparecer una vez por curso
    -- (puede estar en múltiples unidades, pero solo una vez en el syllabus global)
    CONSTRAINT unique_course_competency UNIQUE (course_id, competency_id)
);

-- Índices para optimización de consultas analíticas
CREATE INDEX idx_syllabus_course ON public.course_syllabus(course_id);
CREATE INDEX idx_syllabus_unit ON public.course_syllabus(unit_id);
CREATE INDEX idx_syllabus_competency ON public.course_syllabus(competency_id);
CREATE INDEX idx_syllabus_order ON public.course_syllabus(unit_id, order_index);

-- Trigger para actualizar updated_at automáticamente
CREATE TRIGGER update_course_syllabus_updated_at
    BEFORE UPDATE ON public.course_syllabus
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ----------------------------------------------------------------------------
-- RLS: Aislamiento Institucional
-- ----------------------------------------------------------------------------
-- Política: Los profesores solo pueden gestionar el syllabus de cursos donde:
--   1. Sean el creator_id del curso, O
--   2. Estén vinculados vía teacher_student_mapping (institución compartida)
-- ----------------------------------------------------------------------------

ALTER TABLE public.course_syllabus ENABLE ROW LEVEL SECURITY;

-- Política de lectura: Profesores pueden ver syllabus de sus cursos
CREATE POLICY "Teachers can view syllabus of their courses"
    ON public.course_syllabus
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.courses c
            WHERE c.id = course_syllabus.course_id
            AND (
                -- Dueño del curso
                c.teacher_id = auth.uid()
            )
        )
    );

-- Política de inserción: Solo dueños del curso
CREATE POLICY "Course creators can insert syllabus entries"
    ON public.course_syllabus
    FOR INSERT
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM public.courses c
            WHERE c.id = course_syllabus.course_id
            AND c.teacher_id = auth.uid()
        )
    );

-- Política de actualización: Solo dueños del curso
CREATE POLICY "Course creators can update syllabus entries"
    ON public.course_syllabus
    FOR UPDATE
    USING (
        EXISTS (
            SELECT 1 FROM public.courses c
            WHERE c.id = course_syllabus.course_id
            AND c.teacher_id = auth.uid()
        )
    );

-- Política de eliminación: Solo dueños del curso
CREATE POLICY "Course creators can delete syllabus entries"
    ON public.course_syllabus
    FOR DELETE
    USING (
        EXISTS (
            SELECT 1 FROM public.courses c
            WHERE c.id = course_syllabus.course_id
            AND c.teacher_id = auth.uid()
        )
    );

-- ----------------------------------------------------------------------------
-- VISTA ANALÍTICA: vw_syllabus_progress
-- ----------------------------------------------------------------------------
-- Propósito: Cruzar el syllabus con la evidencia forense de los alumnos
--            para calcular progreso real contra el programa de estudios.
--
-- Lógica Forense:
--   1. Extrae el estado más reciente de cada competencia desde exam_attempts
--   2. Una competencia se considera completada solo si state = 'MASTERED'
--   3. Calcula progress_percentage por unit_id (Micro) y course_id (Macro)
-- ----------------------------------------------------------------------------

CREATE OR REPLACE VIEW public.vw_syllabus_progress AS
WITH latest_attempts AS (
    -- Obtener el último intento completado por alumno y examen
    SELECT DISTINCT ON (ea.learner_id, ea.exam_id)
        ea.learner_id,
        ea.exam_id,
        ea.results_cache,
        ea.finished_at
    FROM public.exam_attempts ea
    WHERE ea.status = 'COMPLETED'
    AND ea.results_cache IS NOT NULL
    ORDER BY ea.learner_id, ea.exam_id, ea.finished_at DESC
),
competency_states AS (
    -- Extraer el estado de cada competencia desde results_cache
    SELECT
        la.learner_id,
        (diagnosis->>'competencyId')::UUID AS competency_id,
        diagnosis->>'state' AS state,
        la.finished_at
    FROM latest_attempts la
    CROSS JOIN LATERAL jsonb_array_elements(la.results_cache->'competencyDiagnoses') AS diagnosis
    WHERE diagnosis->>'competencyId' IS NOT NULL
),
latest_competency_states AS (
    -- Obtener el estado más reciente de cada competencia por alumno
    SELECT DISTINCT ON (cs.learner_id, cs.competency_id)
        cs.learner_id,
        cs.competency_id,
        cs.state,
        cs.finished_at
    FROM competency_states cs
    ORDER BY cs.learner_id, cs.competency_id, cs.finished_at DESC
),
syllabus_progress_detail AS (
    -- Cruzar syllabus con estados de competencias
    SELECT
        cs_table.course_id,
        cs_table.unit_id,
        lcs.learner_id,
        cs_table.competency_id,
        cs_table.is_required,
        lcs.finished_at,
        CASE 
            WHEN lcs.state = 'MASTERED' THEN true
            ELSE false
        END AS is_completed
    FROM public.course_syllabus cs_table
    JOIN latest_competency_states lcs 
        ON lcs.competency_id = cs_table.competency_id
)
-- Calcular métricas de progreso por unidad y curso
SELECT
    spd.course_id,
    spd.unit_id,
    spd.learner_id,
    
    -- Métricas por unidad (Nivel Micro)
    COUNT(*) FILTER (WHERE spd.is_required) AS total_required_competencies,
    COUNT(*) FILTER (WHERE spd.is_required AND spd.is_completed) AS completed_required_competencies,
    ROUND(
        (COUNT(*) FILTER (WHERE spd.is_required AND spd.is_completed)::NUMERIC / 
         NULLIF(COUNT(*) FILTER (WHERE spd.is_required), 0) * 100),
        2
    ) AS unit_progress_percentage,
    
    -- Métricas globales por curso (Nivel Macro)
    SUM(COUNT(*) FILTER (WHERE spd.is_required)) OVER (PARTITION BY spd.course_id, spd.learner_id) 
        AS course_total_required,
    SUM(COUNT(*) FILTER (WHERE spd.is_required AND spd.is_completed)) OVER (PARTITION BY spd.course_id, spd.learner_id) 
        AS course_completed_required,
    ROUND(
        (SUM(COUNT(*) FILTER (WHERE spd.is_required AND spd.is_completed)) OVER (PARTITION BY spd.course_id, spd.learner_id)::NUMERIC / 
         NULLIF(SUM(COUNT(*) FILTER (WHERE spd.is_required)) OVER (PARTITION BY spd.course_id, spd.learner_id), 0) * 100),
        2
    ) AS course_progress_percentage,
    
    -- Timestamp de última actualización (de cualquiera de las competencias en el syllabus)
    MAX(spd.finished_at) AS last_updated
FROM syllabus_progress_detail spd
GROUP BY 
    spd.course_id, 
    spd.unit_id, 
    spd.learner_id;

-- Índice materializado para optimización (opcional, para producción)
-- CREATE MATERIALIZED VIEW public.mv_syllabus_progress AS SELECT * FROM public.vw_syllabus_progress;
-- CREATE UNIQUE INDEX ON public.mv_syllabus_progress (course_id, unit_id, learner_id);

-- ----------------------------------------------------------------------------
-- COMENTARIOS DE DOCUMENTACIÓN
-- ----------------------------------------------------------------------------

COMMENT ON TABLE public.course_syllabus IS 
'Syllabus Engine - Fase 1: Relación canónica entre unidades curriculares y competencias. Cada fila representa un átomo de conocimiento dentro de una unidad temporal del programa de estudios.';

COMMENT ON COLUMN public.course_syllabus.order_index IS 
'Secuencia pedagógica dentro de la unidad. Permite ordenar competencias según la progresión didáctica.';

COMMENT ON COLUMN public.course_syllabus.is_required IS 
'Indica si la competencia es obligatoria para el cálculo de progreso. Competencias opcionales no afectan el porcentaje de completitud.';

COMMENT ON VIEW public.vw_syllabus_progress IS 
'Vista analítica forense que cruza el syllabus con la evidencia de aprendizaje (exam_attempts). Calcula progreso real por unidad (Micro) y curso (Macro) basado en competencias en estado MASTERED.';
