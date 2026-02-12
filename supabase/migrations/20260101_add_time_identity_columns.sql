-- Add stats_metadata to exams for aggregate time analysis
ALTER TABLE public.exams 
ADD COLUMN IF NOT EXISTS stats_metadata JSONB DEFAULT '{}'::jsonb;

COMMENT ON COLUMN public.exams.stats_metadata IS 'Aggregate statistical metadata including avg_expected_time and total_min_time';
