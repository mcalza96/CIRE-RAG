-- Migration: Harden probe_options for Cognitive Structural Defense

-- 1. Add pedagogical_rationale column if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'probe_options' AND column_name = 'pedagogical_rationale') THEN
        ALTER TABLE "public"."probe_options" ADD COLUMN "pedagogical_rationale" text;
    END IF;
END $$;

-- 2. Add immediate_feedback column (aliased as feedback exists, but ensuring consistency)
-- Note: 'feedback' column already exists based on inspection, so we use it.

-- 3. Add CHECK constraint for Structural Defense
-- Rule: If is_correct is FALSE, then diagnoses_misconception_id MUST NOT be NULL.
-- We also enforce that feedback must be present for incorrect answers.
ALTER TABLE "public"."probe_options"
    DROP CONSTRAINT IF EXISTS "check_structural_defense";

ALTER TABLE "public"."probe_options"
    ADD CONSTRAINT "check_structural_defense"
    CHECK (
        (is_correct = true) OR 
        (diagnoses_misconception_id IS NOT NULL AND feedback IS NOT NULL)
    );

COMMENT ON CONSTRAINT "check_structural_defense" ON "public"."probe_options" 
IS 'Structural Defense: Incorrect options must map to a misconception and provide feedback.';
