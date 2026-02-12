-- ========================================================
-- BKT KNOWLEDGE TRACING SCHEMA (v1.0)
-- ========================================================

-- 1. ACTUALIZACIÓN DE COMPETENCY NODES
-- Añadimos parámetros base de BKT.
-- Valores por defecto estándar en la literatura BKT:
-- P(Transit) = 0.1 (Baja probabilidad de aprender por paso)
-- P(Slip) = 0.1 (10% de error por descuido)
-- P(Guess) = 0.2 (20% de probabilidad de acertar por azar)

ALTER TABLE public.competency_nodes 
ADD COLUMN IF NOT EXISTS p_transit_default FLOAT DEFAULT 0.1,
ADD COLUMN IF NOT EXISTS p_slip_default FLOAT DEFAULT 0.1,
ADD COLUMN IF NOT EXISTS p_guess_default FLOAT DEFAULT 0.2;

COMMENT ON COLUMN public.competency_nodes.p_transit_default IS 'Probabilidad base de transicionar al estado de dominio.';
COMMENT ON COLUMN public.competency_nodes.p_slip_default IS 'Probabilidad de cometer un error conociendo la competencia.';
COMMENT ON COLUMN public.competency_nodes.p_guess_default IS 'Probabilidad de acertar sin conocer la competencia.';

-- 2. TABLA DE ESTADOS DE CONOCIMIENTO (KNOWLEDGE STATES)
CREATE TABLE IF NOT EXISTS public.knowledge_states (
    student_id UUID NOT NULL REFERENCES public.learners(id) ON DELETE CASCADE,
    competency_id UUID NOT NULL REFERENCES public.competency_nodes(id) ON DELETE CASCADE,
    p_mastery FLOAT NOT NULL DEFAULT 0.1, -- Probabilidad actual de dominio
    confidence_history JSONB DEFAULT '[]'::jsonb, -- Historial de confianza reportada
    last_attempt_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (student_id, competency_id)
);

-- Indexación para búsquedas rápidas de progreso
CREATE INDEX IF NOT EXISTS idx_ks_student ON public.knowledge_states(student_id);
CREATE INDEX IF NOT EXISTS idx_ks_competency ON public.knowledge_states(competency_id);

-- 3. POLÍTICAS RLS (Seguridad Forense)
ALTER TABLE public.knowledge_states ENABLE ROW LEVEL SECURITY;

-- Estudiantes: Pueden ver sus propios estados de conocimiento
CREATE POLICY "student_view_own_ks" ON public.knowledge_states
FOR SELECT TO authenticated
USING (auth.uid() = student_id);

-- Profesores: Pueden ver los estados de sus estudiantes asignados (Aislamiento Institucional)
CREATE POLICY "teacher_view_assigned_ks" ON public.knowledge_states
FOR SELECT TO authenticated
USING (
    EXISTS (
        SELECT 1 FROM public.teacher_student_mapping tsm
        WHERE tsm.teacher_id = auth.uid()
        AND tsm.student_id = public.knowledge_states.student_id
    )
);

-- Staff: Pueden gestionar estados de conocimiento si es necesario por el motor de inferencia
CREATE POLICY "staff_manage_ks" ON public.knowledge_states
FOR ALL TO authenticated
USING (public.is_staff());

-- 4. TRIGGER PARA UPDATED_AT
CREATE OR REPLACE FUNCTION public.handle_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_knowledge_states_updated_at
    BEFORE UPDATE ON public.knowledge_states
    FOR EACH ROW EXECUTE FUNCTION public.handle_updated_at();
