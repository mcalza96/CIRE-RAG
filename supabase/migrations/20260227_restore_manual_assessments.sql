-- ============================================================================
-- MIGRATION: Restore Manual Assessments & Fix Schema Drift
-- Descripción: Restaura las tablas manual_assessments y manual_criteria_grades, 
--              y agrega la columna faltante en micro_submissions.
-- Fecha: 2026-02-27
-- ============================================================================

BEGIN;

-- 1. Reparar micro_submissions
ALTER TABLE public.micro_submissions 
ADD COLUMN IF NOT EXISTS selected_answer TEXT;

-- 2. Restaurar manual_assessments (Schema Drift Protection)
DO $$ 
BEGIN
    -- Si la tabla existe pero no tiene exam_id, la reparamos
    IF EXISTS (SELECT FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'manual_assessments') THEN
        -- Agregar exam_id si falta
        IF NOT EXISTS (SELECT FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'manual_assessments' AND column_name = 'exam_id') THEN
            ALTER TABLE public.manual_assessments ADD COLUMN exam_id UUID REFERENCES public.exams(id) ON DELETE CASCADE;
            -- Limpiar registros huérfanos para poder aplicar NOT NULL
            DELETE FROM public.manual_assessments WHERE exam_id IS NULL;
            ALTER TABLE public.manual_assessments ALTER COLUMN exam_id SET NOT NULL;
        END IF;

        -- Asegurar que final_score sea FLOAT (double precision)
        ALTER TABLE public.manual_assessments ALTER COLUMN final_score SET DEFAULT 0;
        UPDATE public.manual_assessments SET final_score = 0 WHERE final_score IS NULL;
        ALTER TABLE public.manual_assessments ALTER COLUMN final_score SET NOT NULL;

        -- Asegurar que status sea TEXT con el check adecuado
        -- Primero removemos el default si existe para cambiar el tipo
        ALTER TABLE public.manual_assessments ALTER COLUMN status DROP DEFAULT;
        ALTER TABLE public.manual_assessments ALTER COLUMN status TYPE TEXT USING status::text;
        ALTER TABLE public.manual_assessments ALTER COLUMN status SET DEFAULT 'draft';
        
        -- Reiniciar status inválidos
        UPDATE public.manual_assessments SET status = 'draft' WHERE status NOT IN ('draft', 'completed');
    ELSE
        -- Si no existe, crearla de cero
        CREATE TABLE public.manual_assessments (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            student_id UUID NOT NULL REFERENCES public.learners(id) ON DELETE CASCADE,
            exam_id UUID NOT NULL REFERENCES public.exams(id) ON DELETE CASCADE,
            final_score FLOAT NOT NULL DEFAULT 0,
            feedback_summary TEXT,
            status TEXT NOT NULL CHECK (status IN ('draft', 'completed')) DEFAULT 'draft',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    END IF;
END $$;

-- 3. Restaurar manual_criteria_grades
CREATE TABLE IF NOT EXISTS public.manual_criteria_grades (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    assessment_id UUID NOT NULL REFERENCES public.manual_assessments(id) ON DELETE CASCADE,
    criterion_id UUID NOT NULL, -- Referencia a la rúbrica/criterio
    score_obtained FLOAT NOT NULL DEFAULT 0,
    feedback_comment TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT unique_criterion_per_assessment UNIQUE(assessment_id, criterion_id)
);

-- 4. Índices para Performance
CREATE INDEX IF NOT EXISTS idx_manual_assessments_student ON public.manual_assessments(student_id);
CREATE INDEX IF NOT EXISTS idx_manual_assessments_exam ON public.manual_assessments(exam_id);
CREATE INDEX IF NOT EXISTS idx_manual_criteria_grades_assessment ON public.manual_criteria_grades(assessment_id);

-- 5. Row Level Security (RLS)
ALTER TABLE public.manual_assessments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.manual_criteria_grades ENABLE ROW LEVEL SECURITY;

-- 5.1 Políticas para manual_assessments
DROP POLICY IF EXISTS "manual_assessments_student_read" ON public.manual_assessments;
CREATE POLICY "manual_assessments_student_read" ON public.manual_assessments
FOR SELECT TO authenticated
USING (
    student_id = auth.uid() OR
    public.is_staff()
);

DROP POLICY IF EXISTS "manual_assessments_staff_manage" ON public.manual_assessments;
CREATE POLICY "manual_assessments_staff_manage" ON public.manual_assessments
FOR ALL TO authenticated
USING (public.is_staff())
WITH CHECK (public.is_staff());

-- 5.2 Políticas para manual_criteria_grades
DROP POLICY IF EXISTS "manual_criteria_grades_student_read" ON public.manual_criteria_grades;
CREATE POLICY "manual_criteria_grades_student_read" ON public.manual_criteria_grades
FOR SELECT TO authenticated
USING (
    EXISTS (
        SELECT 1 FROM public.manual_assessments ma
        WHERE ma.id = assessment_id AND ma.student_id = auth.uid()
    ) OR
    public.is_staff()
);

DROP POLICY IF EXISTS "manual_criteria_grades_staff_manage" ON public.manual_criteria_grades;
CREATE POLICY "manual_criteria_grades_staff_manage" ON public.manual_criteria_grades
FOR ALL TO authenticated
USING (public.is_staff())
WITH CHECK (public.is_staff());

COMMIT;
