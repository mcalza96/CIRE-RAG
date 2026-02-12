-- Teacher Alerts Table
-- Stores critical alerts requiring teacher attention (remediation failures, etc.)
CREATE TABLE IF NOT EXISTS public.teacher_alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id UUID REFERENCES auth.users(id),
    student_id UUID NOT NULL REFERENCES public.learners(id) ON DELETE CASCADE,
    concept_id UUID REFERENCES public.competency_nodes(id) ON DELETE SET NULL,
    lesson_id UUID REFERENCES public.lessons(id) ON DELETE SET NULL,
    reason TEXT NOT NULL CHECK (reason IN ('remediation_failed', 'persistent_gap', 'skill_degradation', 'intervention_needed')),
    severity TEXT NOT NULL DEFAULT 'MEDIUM' CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    is_read BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ,
    notes TEXT
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_teacher_alerts_teacher_id ON public.teacher_alerts(teacher_id);
CREATE INDEX IF NOT EXISTS idx_teacher_alerts_student_id ON public.teacher_alerts(student_id);
CREATE INDEX IF NOT EXISTS idx_teacher_alerts_is_read ON public.teacher_alerts(is_read);
CREATE INDEX IF NOT EXISTS idx_teacher_alerts_created_at ON public.teacher_alerts(created_at DESC);

-- Composite index for common dashboard query
CREATE INDEX IF NOT EXISTS idx_teacher_alerts_unread ON public.teacher_alerts(teacher_id, is_read, created_at DESC)
WHERE is_read = FALSE;

-- Enable RLS
ALTER TABLE public.teacher_alerts ENABLE ROW LEVEL SECURITY;

-- RLS Policies
-- Teachers can only see their own alerts (assigned via teacher_student_mapping or directly)
CREATE POLICY "teachers_view_own_alerts"
ON public.teacher_alerts FOR SELECT
TO authenticated
USING (
    teacher_id = auth.uid()
    OR EXISTS (
        SELECT 1 FROM public.teacher_student_mapping tsm
        WHERE tsm.teacher_id = auth.uid() AND tsm.student_id = teacher_alerts.student_id
    )
);

-- Teachers can update (mark as read, add notes) their own alerts
CREATE POLICY "teachers_update_own_alerts"
ON public.teacher_alerts FOR UPDATE
TO authenticated
USING (
    teacher_id = auth.uid()
    OR EXISTS (
        SELECT 1 FROM public.teacher_student_mapping tsm
        WHERE tsm.teacher_id = auth.uid() AND tsm.student_id = teacher_alerts.student_id
    )
);

-- Allow inserts from service (for remediation-logic.ts)
CREATE POLICY "service_insert_alerts"
ON public.teacher_alerts FOR INSERT
TO authenticated
WITH CHECK (true);
