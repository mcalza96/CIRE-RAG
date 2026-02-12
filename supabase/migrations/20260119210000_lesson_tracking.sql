-- Create lesson_tracking table to persist student progress within a lesson
CREATE TABLE IF NOT EXISTS public.lesson_tracking (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id UUID NOT NULL REFERENCES public.learners(id) ON DELETE CASCADE,
    lesson_id UUID NOT NULL REFERENCES public.lessons(id) ON DELETE CASCADE,
    current_phase TEXT NOT NULL DEFAULT 'ENTRY_TICKET',
    completed_bridge BOOLEAN DEFAULT FALSE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    UNIQUE(student_id, lesson_id)
);

-- Enable RLS
ALTER TABLE public.lesson_tracking ENABLE ROW LEVEL SECURITY;

-- Policies
DROP POLICY IF EXISTS "Students can view their own tracking" ON public.lesson_tracking;
CREATE POLICY "Students can view their own tracking"
    ON public.lesson_tracking FOR SELECT
    USING (student_id = auth.uid());

DROP POLICY IF EXISTS "Students can update their own tracking" ON public.lesson_tracking;
CREATE POLICY "Students can update their own tracking"
    ON public.lesson_tracking FOR UPDATE
    USING (student_id = auth.uid());

DROP POLICY IF EXISTS "Students can insert their own tracking" ON public.lesson_tracking;
CREATE POLICY "Students can insert their own tracking"
    ON public.lesson_tracking FOR INSERT
    WITH CHECK (student_id = auth.uid());
