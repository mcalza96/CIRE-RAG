-- Create student cognitive profiles table
-- This table stores the metacognitive calibration metrics for each student per course.

CREATE TABLE IF NOT EXISTS public.student_cognitive_profiles (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    student_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    course_id UUID NOT NULL REFERENCES public.courses(id) ON DELETE CASCADE,
    
    -- Calibration Metrics (from MetacognitiveAnalyzer output)
    ece_score NUMERIC(5,2) NOT NULL DEFAULT 0,
    certainty_average INTEGER NOT NULL DEFAULT 0,
    accuracy_average INTEGER NOT NULL DEFAULT 0,
    calibration_status TEXT NOT NULL DEFAULT 'CALIBRATED' CHECK (calibration_status IN ('CALIBRATED', 'OVERCONFIDENT', 'UNDERCONFIDENT', 'DELUSIONAL')),
    blind_spots_count INTEGER NOT NULL DEFAULT 0,
    fragile_knowledge_count INTEGER NOT NULL DEFAULT 0,
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    
    -- Constraint: Only one profile per student per course
    CONSTRAINT unique_student_course_profile UNIQUE (student_id, course_id)
);

-- Index for efficient lookups
CREATE INDEX IF NOT EXISTS idx_cognitive_profiles_student ON public.student_cognitive_profiles(student_id);
CREATE INDEX IF NOT EXISTS idx_cognitive_profiles_course ON public.student_cognitive_profiles(course_id);

-- RLS Policies
ALTER TABLE public.student_cognitive_profiles ENABLE ROW LEVEL SECURITY;

-- Students can read their own profiles
CREATE POLICY "Students can view their own cognitive profiles"
    ON public.student_cognitive_profiles
    FOR SELECT
    USING (auth.uid() = student_id);

-- System (service role) can insert/update profiles
CREATE POLICY "Service role can manage cognitive profiles"
    ON public.student_cognitive_profiles
    FOR ALL
    USING (true)
    WITH CHECK (true);

-- Comment
COMMENT ON TABLE public.student_cognitive_profiles IS 'Stores metacognitive calibration data for each student per course, tracking blind spots and fragile knowledge.';
