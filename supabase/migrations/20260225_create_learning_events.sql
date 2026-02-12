-- Migration: Create unified learning_events table
-- Captures all learning signals from micro_assessment and manual_assessment sources

CREATE TABLE IF NOT EXISTS public.learning_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id UUID NOT NULL REFERENCES public.learners(id) ON DELETE CASCADE,
    competency_id UUID REFERENCES public.competency_nodes(id) ON DELETE SET NULL,
    source TEXT NOT NULL CHECK (source IN ('micro_assessment', 'manual_assessment')),
    source_id UUID NOT NULL,
    outcome JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_learning_events_student
ON public.learning_events(student_id);

CREATE INDEX IF NOT EXISTS idx_learning_events_competency
ON public.learning_events(competency_id);

CREATE INDEX IF NOT EXISTS idx_learning_events_source
ON public.learning_events(source, source_id);

CREATE INDEX IF NOT EXISTS idx_learning_events_created
ON public.learning_events(created_at DESC);

-- Enable RLS
ALTER TABLE public.learning_events ENABLE ROW LEVEL SECURITY;

-- Policy: Teachers can see events for students in their cohorts
CREATE POLICY "Teachers view cohort learning events"
ON public.learning_events
FOR SELECT
USING (
    EXISTS (
        SELECT 1 FROM public.cohort_members cm
        JOIN public.cohorts c ON c.id = cm.cohort_id
        WHERE cm.student_id = learning_events.student_id
        AND c.teacher_id = auth.uid()
    )
);

-- Policy: System can insert (service role)
CREATE POLICY "System inserts learning events"
ON public.learning_events
FOR INSERT
WITH CHECK (true);

COMMENT ON TABLE public.learning_events IS 
'Unified audit trail capturing learning signals from both micro (F2) and manual (F4) assessments.';

COMMENT ON COLUMN public.learning_events.outcome IS 
'JSONB containing: { isCorrect, diagnosis, score, maxScore, ... } depending on source.';
