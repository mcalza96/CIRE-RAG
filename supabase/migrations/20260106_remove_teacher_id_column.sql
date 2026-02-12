-- CLEANUP: Remove obsolete teacher_id column from learners.
-- Relationships are now handled via teacher_student_mapping (M:N) table.
-- introduced in: 20250102183000_teacher_student_mn.sql

DO $$
DECLARE
    learner_count INT;
    mapping_count INT;
BEGIN
    -- Safety Check: Ensure data is migrated before dropping the column
    SELECT COUNT(*) INTO learner_count FROM public.learners;
    SELECT COUNT(*) INTO mapping_count FROM public.teacher_student_mapping;
    
    -- If we have learners but no mappings, it's unsafe to drop the legacy column
    IF learner_count > 0 AND mapping_count = 0 THEN
        RAISE EXCEPTION 'CRITICAL: Data Migration Incomplete. Learners exist but teacher_student_mapping is empty. Aborting column drop.';
    END IF;

    -- Proceed if safe
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'learners' AND column_name = 'teacher_id') THEN
        ALTER TABLE public.learners DROP COLUMN teacher_id;
    END IF;
END $$;
