-- Migration: Extend remedial_content for F4 manual grading support
-- Allows remediation to originate from either micro_submissions OR manual_criteria_grades

-- 1. Add manual grading FK column
ALTER TABLE public.remedial_content
ADD COLUMN IF NOT EXISTS manual_criteria_grade_id UUID
REFERENCES public.manual_criteria_grades(id) ON DELETE CASCADE;

-- 2. Make submission_id nullable (was NOT NULL, now either source is valid)
ALTER TABLE public.remedial_content
ALTER COLUMN submission_id DROP NOT NULL;

-- 3. Add check constraint: must have at least one source
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'remedial_content_source_check'
    ) THEN
        ALTER TABLE public.remedial_content
        ADD CONSTRAINT remedial_content_source_check
        CHECK (submission_id IS NOT NULL OR manual_criteria_grade_id IS NOT NULL);
    END IF;
END $$;

-- 4. Create indexes for both source types
CREATE INDEX IF NOT EXISTS idx_remedial_content_manual_grade
ON public.remedial_content(manual_criteria_grade_id);

COMMENT ON COLUMN public.remedial_content.manual_criteria_grade_id IS 
'FK to manual_criteria_grades for F4 rubric-based remediation. Mutually exclusive with submission_id.';
