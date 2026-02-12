-- ============================================================================
-- MIGRATION: Stealth Assessment Infrastructure (MicroAssessments)
-- Descripción: Implementa el banco de preguntas ligero y el registro de pulsos.
-- Fecha: 2026-02-02
-- ============================================================================

BEGIN;

-- 1. Tablas de Micro-Evaluación
-- ----------------------------------------------------------------------------

-- Tabla: micro_assessments (El Banco de Preguntas Efímero)
CREATE TABLE IF NOT EXISTS public.micro_assessments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lesson_id UUID NOT NULL REFERENCES public.lessons(id) ON DELETE CASCADE,
    content_block_id TEXT, -- ID del bloque en BlockNote para diagnóstico preciso
    type TEXT NOT NULL CHECK (type IN ('verification', 'recall')),
    payload JSONB NOT NULL, -- { question: string, options: string[], correct: string, blooms_level: string }
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Tabla: micro_submissions (El Registro de Pulsos)
CREATE TABLE IF NOT EXISTS public.micro_submissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id UUID NOT NULL REFERENCES public.learners(id) ON DELETE CASCADE,
    micro_assessment_id UUID NOT NULL REFERENCES public.micro_assessments(id) ON DELETE CASCADE,
    is_correct BOOLEAN NOT NULL,
    response_time_ms INTEGER NOT NULL,
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    -- Restricción: Un estudiante solo puede responder una vez por micro-evaluación
    CONSTRAINT unique_student_micro_assessment UNIQUE(student_id, micro_assessment_id)
);

-- 2. Índices Estratégicos (Performance)
-- ----------------------------------------------------------------------------

-- Optimización Ticket de Entrada/Salida
CREATE INDEX IF NOT EXISTS idx_micro_assessments_lesson_type 
ON public.micro_assessments(lesson_id, type);

-- Optimización de consultas de progreso por estudiante
CREATE INDEX IF NOT EXISTS idx_micro_submissions_student_assessment
ON public.micro_submissions(student_id, micro_assessment_id);

-- 3. Row Level Security (RLS)
-- ----------------------------------------------------------------------------

ALTER TABLE public.micro_assessments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.micro_submissions ENABLE ROW LEVEL SECURITY;

-- 3.1 Políticas para micro_assessments

-- Lectura: Estudiantes inscritos en el curso de la lección pueden ver las preguntas
CREATE POLICY "micro_assessments_read_access" ON public.micro_assessments
FOR SELECT TO authenticated
USING (
    public.is_staff() OR
    EXISTS (
        SELECT 1 FROM public.lessons l
        JOIN public.courses c ON c.id = l.course_id
        JOIN public.teacher_student_mapping tsm ON tsm.teacher_id = c.teacher_id
        WHERE l.id = public.micro_assessments.lesson_id
        AND tsm.student_id = auth.uid()
    )
);

-- Gestión: Solo Staff puede crear/editar/borrar
CREATE POLICY "micro_assessments_staff_management" ON public.micro_assessments
FOR ALL TO authenticated
USING (public.is_staff())
WITH CHECK (public.is_staff());

-- 3.2 Políticas para micro_submissions

-- Lectura: Estudiantes ven sus propias respuestas; Staff ve las de sus alumnos
CREATE POLICY "micro_submissions_read_access" ON public.micro_submissions
FOR SELECT TO authenticated
USING (
    student_id = auth.uid() OR
    public.is_admin() OR
    (
        public.is_staff() AND
        EXISTS (
            SELECT 1 FROM public.teacher_student_mapping tsm
            WHERE tsm.student_id = public.micro_submissions.student_id
            AND tsm.teacher_id = auth.uid()
        )
    )
);

-- Inserción: Estudiantes pueden insertar sus propios pulsos
CREATE POLICY "micro_submissions_student_insert" ON public.micro_submissions
FOR INSERT TO authenticated
WITH CHECK (student_id = auth.uid());

-- Gestión Admin: Acceso total para administradores
CREATE POLICY "micro_submissions_admin_management" ON public.micro_submissions
FOR ALL TO authenticated
USING (public.is_admin());

-- 4. Comentarios de Documentación
-- ----------------------------------------------------------------------------

COMMENT ON TABLE public.micro_assessments IS 
'Almacena preguntas atómicas de baja latencia (Entry/Exit tickets) vinculadas a lecciones para Stealth Assessment.';

COMMENT ON TABLE public.micro_submissions IS 
'Registro de respuestas rápidas de estudiantes a micro-evaluaciones para medir pulso de aprendizaje en tiempo real.';

COMMIT;
