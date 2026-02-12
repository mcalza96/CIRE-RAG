-- Migration: Misconception Feedback (RLHF System)
-- Description: Enables teachers to report false positives in AI diagnostics
-- Date: 2026-01-17

-- 1. Create misconception_feedback table
CREATE TABLE IF NOT EXISTS public.misconception_feedback (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id UUID NOT NULL REFERENCES public.exam_attempts(id) ON DELETE CASCADE,
    question_id UUID NOT NULL,
    misconception_id UUID NOT NULL REFERENCES public.competency_nodes(id) ON DELETE CASCADE,
    is_false_positive BOOLEAN NOT NULL,
    teacher_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    teacher_notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    
    -- Prevent duplicate feedback for same question/misconception in same attempt
    UNIQUE(attempt_id, question_id, misconception_id)
);

-- 2. Enable RLS
ALTER TABLE public.misconception_feedback ENABLE ROW LEVEL SECURITY;

-- 3. RLS Policies
DROP POLICY IF EXISTS "Teachers can view all feedback" ON public.misconception_feedback;
CREATE POLICY "Teachers can view all feedback"
ON public.misconception_feedback FOR SELECT
TO authenticated
USING (
    public.role_is('admin'::public.app_role) OR 
    public.role_is('instructor'::public.app_role)
);

DROP POLICY IF EXISTS "Teachers can insert feedback" ON public.misconception_feedback;
CREATE POLICY "Teachers can insert feedback"
ON public.misconception_feedback FOR INSERT
TO authenticated
WITH CHECK (
    public.role_is('admin'::public.app_role) OR 
    public.role_is('instructor'::public.app_role)
);

-- 4. Indexes for analytics
CREATE INDEX IF NOT EXISTS misconception_feedback_misconception_idx 
ON public.misconception_feedback(misconception_id, is_false_positive);

CREATE INDEX IF NOT EXISTS misconception_feedback_teacher_idx 
ON public.misconception_feedback(teacher_id, created_at DESC);

CREATE INDEX IF NOT EXISTS misconception_feedback_attempt_idx 
ON public.misconception_feedback(attempt_id);

-- 5. Materialized view for AI precision metrics
CREATE MATERIALIZED VIEW IF NOT EXISTS public.misconception_precision_metrics AS
SELECT 
    m.misconception_id,
    cn.title as misconception_title,
    COUNT(*) as total_diagnoses,
    COUNT(*) FILTER (WHERE m.is_false_positive = true) as false_positives,
    COUNT(*) FILTER (WHERE m.is_false_positive = false) as true_positives,
    ROUND(
        (COUNT(*) FILTER (WHERE m.is_false_positive = false)::NUMERIC / COUNT(*)::NUMERIC) * 100, 
        2
    ) as precision_percentage,
    MAX(m.created_at) as last_feedback_at
FROM public.misconception_feedback m
JOIN public.competency_nodes cn ON cn.id = m.misconception_id
GROUP BY m.misconception_id, cn.title
HAVING COUNT(*) >= 3; -- Only show metrics with at least 3 feedback samples

-- 6. Index on materialized view
CREATE UNIQUE INDEX IF NOT EXISTS misconception_precision_metrics_idx 
ON public.misconception_precision_metrics(misconception_id);

-- 7. Refresh function (call this periodically via cron or after feedback)
CREATE OR REPLACE FUNCTION public.refresh_misconception_precision_metrics()
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY public.misconception_precision_metrics;
END;
$$;

GRANT EXECUTE ON FUNCTION public.refresh_misconception_precision_metrics() TO service_role;

-- 8. Comments
COMMENT ON TABLE misconception_feedback IS 
'RLHF system: Teachers report false positives in AI diagnostics. Used to improve misconception detection accuracy.';

COMMENT ON MATERIALIZED VIEW misconception_precision_metrics IS 
'Aggregated precision metrics per misconception. Refresh via refresh_misconception_precision_metrics().';
