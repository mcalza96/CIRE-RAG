-- =============================================================================
-- MIGRATION: RUBRIC CONSTITUTION (IA Constitucional)
-- Description: Adds JSONB column for storing processed rubric constitution
--              with Hard/Soft constraints for governing course generation.
-- Date: 2026-02-05
-- =============================================================================

BEGIN;

-- 1. ADD CONSTITUTION COLUMN
-- Stores the structured JSON output from RubricTransmuter

ALTER TABLE public.rubrics 
ADD COLUMN IF NOT EXISTS constitution_json JSONB;

-- Add comment for documentation
COMMENT ON COLUMN public.rubrics.constitution_json IS 
'Structured constitution derived from rubric via AI transmutation. Contains hardConstraints (mandatory rules) and softConstraints (pedagogical DNA). See RubricConstitutionSchema.';

-- 2. GIN INDEX FOR JSONB QUERIES
-- Enables fast queries on constitution fields (e.g., searching by mandatoryTopics)

CREATE INDEX IF NOT EXISTS idx_rubrics_constitution_gin 
ON public.rubrics USING gin (constitution_json);

-- Additional index for specific lookups on hardConstraints
CREATE INDEX IF NOT EXISTS idx_rubrics_constitution_hard_constraints
ON public.rubrics USING gin ((constitution_json -> 'hardConstraints'));

-- 3. VALIDATION CONSTRAINT
-- Ensures the constitution has the minimum required structure if present

ALTER TABLE public.rubrics
ADD CONSTRAINT chk_constitution_structure
CHECK (
    constitution_json IS NULL 
    OR (
        constitution_json ? 'hardConstraints' 
        AND constitution_json ? 'softConstraints'
        AND constitution_json ? 'metadata'
    )
);

-- 4. ADD HELPER COLUMN FOR REVIEW STATUS
-- Tracks if the constitution needs human review

ALTER TABLE public.rubrics
ADD COLUMN IF NOT EXISTS constitution_needs_review BOOLEAN DEFAULT FALSE;

COMMENT ON COLUMN public.rubrics.constitution_needs_review IS 
'True if AI extraction had low confidence and requires human intervention before use.';

-- 5. ADD EXTRACTION CONFIDENCE
-- Stores the confidence score from the AI extraction

ALTER TABLE public.rubrics
ADD COLUMN IF NOT EXISTS constitution_confidence DECIMAL(3,2) DEFAULT NULL;

COMMENT ON COLUMN public.rubrics.constitution_confidence IS 
'AI extraction confidence score (0.00-1.00). Values below 0.5 trigger review requirements.';

-- 6. RPC FUNCTION FOR QUERYING MANDATORY TOPICS
-- Enables efficient search for courses requiring specific topics

CREATE OR REPLACE FUNCTION public.find_rubrics_with_topic(topic_query TEXT)
RETURNS SETOF public.rubrics
LANGUAGE sql
STABLE
AS $$
    SELECT r.*
    FROM public.rubrics r
    WHERE r.constitution_json IS NOT NULL
    AND EXISTS (
        SELECT 1
        FROM jsonb_array_elements_text(r.constitution_json -> 'hardConstraints' -> 'mandatoryTopics') AS topic
        WHERE topic ILIKE '%' || topic_query || '%'
    );
$$;

-- Grant execute to authenticated users
GRANT EXECUTE ON FUNCTION public.find_rubrics_with_topic(TEXT) TO authenticated;

COMMIT;
