-- ============================================================================
-- GUÍA DE CONSOLIDACIÓN: Políticas RLS para Modelo Institucional (M:N)
-- ============================================================================
-- 
-- CONTEXTO:
-- Las migraciones archivadas (parent_achievements_schema.sql, submissions_schema.sql)
-- contienen políticas RLS obsoletas que asumen una relación 1:N (parent_id).
--
-- El modelo institucional usa teacher_student_mapping (M:N), donde un estudiante
-- puede tener múltiples profesores y viceversa.
--
-- Este script documenta el patrón correcto para políticas RLS.
-- ============================================================================

-- PATRÓN OBSOLETO (NO USAR):
-- USING (learner_id IN (SELECT id FROM public.learners WHERE parent_id = auth.uid()))

-- PATRÓN CORRECTO (Modelo M:N):
-- USING (learner_id IN (
--     SELECT student_id 
--     FROM public.teacher_student_mapping 
--     WHERE teacher_id = auth.uid()
-- ))

-- ============================================================================
-- EJEMPLO: Política RLS para learner_achievements
-- ============================================================================

DROP POLICY IF EXISTS "Padres ven logros de sus alumnos" ON public.learner_achievements;

CREATE POLICY "Docentes ven logros de sus estudiantes" 
ON public.learner_achievements 
FOR SELECT 
USING (
    learner_id IN (
        SELECT student_id 
        FROM public.teacher_student_mapping 
        WHERE teacher_id = auth.uid()
    )
);

-- ============================================================================
-- EJEMPLO: Política RLS para feedback_messages
-- ============================================================================

DROP POLICY IF EXISTS "Padres ven mensajes de sus alumnos" ON public.feedback_messages;

CREATE POLICY "Docentes ven mensajes de sus estudiantes" 
ON public.feedback_messages 
FOR SELECT 
USING (
    teacher_id = auth.uid()
);

-- ============================================================================
-- EJEMPLO: Política RLS para submissions
-- ============================================================================

DROP POLICY IF EXISTS "Los alumnos ven sus propias entregas" ON public.submissions;
DROP POLICY IF EXISTS "Los padres pueden subir entregas para sus alumnos" ON public.submissions;
DROP POLICY IF EXISTS "Los padres pueden borrar entregas de sus alumnos" ON public.submissions;

-- Política para que docentes vean entregas de sus estudiantes
CREATE POLICY "Docentes ven entregas de sus estudiantes" 
ON public.submissions 
FOR SELECT 
USING (
    learner_id IN (
        SELECT student_id 
        FROM public.teacher_student_mapping 
        WHERE teacher_id = auth.uid()
    )
);

-- ============================================================================
-- ÍNDICES RECOMENDADOS PARA RENDIMIENTO
-- ============================================================================

-- Estos índices mejoran el rendimiento de las consultas RLS
CREATE INDEX IF NOT EXISTS idx_tsm_teacher 
ON public.teacher_student_mapping(teacher_id);

CREATE INDEX IF NOT EXISTS idx_tsm_student 
ON public.teacher_student_mapping(student_id);

-- ============================================================================
-- NOTAS TÉCNICAS
-- ============================================================================
--
-- 1. SEGURIDAD: Las políticas RLS deben validar acceso a través de
--    teacher_student_mapping, NO mediante columnas directas.
--
-- 2. RENDIMIENTO: Los índices en teacher_student_mapping son críticos
--    para evitar table scans en cada validación RLS.
--
-- 3. AUDITORÍA: Todas las políticas deben usar auth.uid() para validar
--    la identidad del usuario autenticado.
--
-- 4. MIGRACIÓN: Este script NO modifica datos existentes, solo actualiza
--    las políticas de seguridad para usar el modelo M:N correcto.
--
-- ============================================================================
