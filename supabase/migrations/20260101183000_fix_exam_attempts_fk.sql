-- Migration: Fix Exam Attempts Foreign Key
-- Description: Redirects the fk_learner constraint to reference 'profiles' instead of the problematic 'users' table.

DO $$
BEGIN
    -- 1. Try to drop the existing constraint (named fk_learner based on error logs)
    IF EXISTS (SELECT 1 FROM information_schema.table_constraints WHERE constraint_name = 'fk_learner' AND table_name = 'exam_attempts') THEN
        ALTER TABLE public.exam_attempts DROP CONSTRAINT fk_learner;
    END IF;

    -- 2. Also check for standard naming convention explicitly just in case
    IF EXISTS (SELECT 1 FROM information_schema.table_constraints WHERE constraint_name = 'exam_attempts_learner_id_fkey' AND table_name = 'exam_attempts') THEN
        ALTER TABLE public.exam_attempts DROP CONSTRAINT exam_attempts_learner_id_fkey;
    END IF;

    -- 3. Re-add the constraint pointing to public.profiles
    -- We assume public.profiles is the correct source of truth for user existence as per repository usage.
    ALTER TABLE public.exam_attempts
    ADD CONSTRAINT fk_learner
    FOREIGN KEY (learner_id)
    REFERENCES public.profiles(id)
    ON DELETE CASCADE;

END $$;
