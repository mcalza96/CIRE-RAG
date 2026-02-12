-- Migration: Add competency FK to micro_assessments
-- Unifica F2 con el vocabulario relacional de competencias

-- 1. Add competency_id column with FK constraint
ALTER TABLE public.micro_assessments
ADD COLUMN IF NOT EXISTS competency_id UUID REFERENCES public.competency_nodes(id) ON DELETE SET NULL;

-- 2. Create index for efficient querying
CREATE INDEX IF NOT EXISTS idx_micro_assessments_competency
ON public.micro_assessments(competency_id);

-- 3. Optional backfill: Extract competency_id from JSONB payload
-- Only run if you want to migrate existing data
-- UPDATE public.micro_assessments
-- SET competency_id = (payload->>'competencyId')::uuid
-- WHERE competency_id IS NULL
--   AND payload->>'competencyId' IS NOT NULL
--   AND payload->>'competencyId' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';

COMMENT ON COLUMN public.micro_assessments.competency_id IS 
'FK to competency_nodes. Also persisted in payload.competencyId for backward compatibility.';
