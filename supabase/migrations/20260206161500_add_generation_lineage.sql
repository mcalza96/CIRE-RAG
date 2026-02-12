-- Migration: Add Generation Trace Lineage (Phase 7 - HITL)
-- Description: Adds traceability between generated content and AI execution traces.
-- Created: 2026-02-06

-- 1. Add generation_trace_id to main entities
ALTER TABLE public.courses 
ADD COLUMN IF NOT EXISTS generation_trace_id UUID;

ALTER TABLE public.course_units 
ADD COLUMN IF NOT EXISTS generation_trace_id UUID;

ALTER TABLE public.lessons 
ADD COLUMN IF NOT EXISTS generation_trace_id UUID;

-- 2. Add indexes for audit performance
CREATE INDEX IF NOT EXISTS idx_courses_generation_trace_id ON public.courses(generation_trace_id);
CREATE INDEX IF NOT EXISTS idx_course_units_generation_trace_id ON public.course_units(generation_trace_id);
CREATE INDEX IF NOT EXISTS idx_lessons_generation_trace_id ON public.lessons(generation_trace_id);

-- 3. Create AI Feedback Events table
CREATE TABLE IF NOT EXISTS public.ai_feedback_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id UUID NOT NULL,
    entity_id UUID, -- Optional: Link to the course/unit/lesson
    event_type TEXT NOT NULL, -- 'accepted', 'edited', 'rejected', 'heavy_edit'
    user_id UUID REFERENCES auth.users(id),
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- 4. Enable RLS
ALTER TABLE public.ai_feedback_events ENABLE ROW LEVEL SECURITY;

-- 5. Policies
CREATE POLICY "Users can insert their own feedback"
ON public.ai_feedback_events
FOR INSERT
WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Admins can view all feedback"
ON public.ai_feedback_events
FOR SELECT
USING (
    EXISTS (
        SELECT 1 FROM public.profiles
        WHERE id = auth.uid() AND role IN ('admin', 'instructor')
    )
);

-- 6. Commentary
COMMENT ON COLUMN public.courses.generation_trace_id IS 'UUID linking this course to the AI generation trace (Forensic Traceability)';
COMMENT ON TABLE public.ai_feedback_events IS 'Audit log for Human-in-the-Loop feedback on AI generated content';
