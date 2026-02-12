-- Migration: Exam Versioning & Forensic Iteration
-- Description: Adds versioning support to exams table and updates inmutability logic.
-- Date: 2026-01-05

-- 1. Add Versioning Columns
ALTER TABLE public.exams 
ADD COLUMN IF NOT EXISTS version INT DEFAULT 1 NOT NULL,
ADD COLUMN IF NOT EXISTS parent_exam_id UUID REFERENCES public.exams(id) ON DELETE SET NULL;

-- 2. Create Unique Index for Versioning
-- Business Rule: (parent_exam_id, version) must be unique to prevent fork collisions.
-- We apply it only where parent_exam_id is not null (forks).
CREATE UNIQUE INDEX IF NOT EXISTS idx_exams_parent_version 
ON public.exams (parent_exam_id, version) 
WHERE parent_exam_id IS NOT NULL;

-- 3. Update Trigger Function: enforce_exam_inmutability
-- Refined Business Rule: 
-- - Blocking structural updates on PUBLISHED exams.
-- - Forcing a version fork for any pedagogical improvement.
CREATE OR REPLACE FUNCTION public.enforce_exam_inmutability()
RETURNS TRIGGER AS $$
BEGIN
    -- Logic for 'exams' table
    IF TG_TABLE_NAME = 'exams' THEN
        -- If it was PUBLISHED, block structural changes
        IF (OLD.status::text = 'PUBLISHED') AND (NEW.status::text = 'PUBLISHED') THEN
            -- We block config_json, questions, and any future structural fields
            IF (NEW.config_json IS DISTINCT FROM OLD.config_json OR 
                NEW.q_matrix IS DISTINCT FROM OLD.q_matrix) THEN
                RAISE EXCEPTION 'Forensic Integrity Violation: Cannot modify a PUBLISHED exam. Please create a NEW VERSION (v%) instead.', OLD.version + 1;
            END IF;
        END IF;
    END IF;

    -- Logic for 'exam_attempts' table: Snapshot Protection
    IF TG_TABLE_NAME = 'exam_attempts' THEN
        IF (NEW.config_snapshot IS DISTINCT FROM OLD.config_snapshot) THEN
            RAISE EXCEPTION 'Forensic Integrity Violation: config_snapshot is immutable after attempt initiation.';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
