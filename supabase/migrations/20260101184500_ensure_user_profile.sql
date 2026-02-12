-- Migration: Ensure User Student Profile and Correct FK
-- Description: Corrects the fk_learner constraint to point to public.learners instead of profiles,
--              avoiding dependencies on auth.users for student attempts.

-- 1. Correct the foreign key for exam_attempts
-- We use DO block for safer execution if running multiple times
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.table_constraints WHERE constraint_name = 'fk_learner' AND table_name = 'exam_attempts') THEN
        ALTER TABLE public.exam_attempts DROP CONSTRAINT fk_learner;
    END IF;
END $$;

ALTER TABLE public.exam_attempts
ADD CONSTRAINT fk_learner
FOREIGN KEY (learner_id)
REFERENCES public.learners(id)
ON DELETE CASCADE;

-- 2. Ensure the specific test learner exists in public.learners
-- We associate it with a teacher/admin if possible.
INSERT INTO public.learners (id, teacher_id, display_name, level)
VALUES (
    'a111c7c6-df45-4c7b-a3b6-1a77466449ed',
    (SELECT id FROM public.profiles WHERE role = 'admin' LIMIT 1),
    'Admin Student',
    1
)
ON CONFLICT (id) DO NOTHING;

-- 3. Optimization: Ensure the trigger for auto-profile (for Teachers) remains intact
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger AS $$
BEGIN
  INSERT INTO public.profiles (id, email, full_name, role)
  VALUES (new.id, new.email, new.raw_user_meta_data->>'full_name', 'teacher')
  ON CONFLICT (id) DO NOTHING;
  RETURN new;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE PROCEDURE public.handle_new_user();
