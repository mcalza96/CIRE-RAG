-- Migration: Add confidence column to micro_submissions
-- Description: Supports Confidence-Based Assessment (CBA) by storing the student's self-reported confidence level.

DO $$ 
BEGIN 
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'micro_submissions' AND column_name = 'confidence') THEN
        ALTER TABLE public.micro_submissions
        ADD COLUMN confidence TEXT DEFAULT 'MEDIUM' NOT NULL CHECK (confidence IN ('LOW', 'MEDIUM', 'HIGH', 'NONE'));
        
        COMMENT ON COLUMN public.micro_submissions.confidence IS 'Confidence Based Assessment (CBA) level: LOW, MEDIUM, HIGH. Default NONE/MEDIUM for legacy data.';
    END IF;
END $$;
