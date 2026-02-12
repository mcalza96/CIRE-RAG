-- Add pedagogical_profile column with initial skeleton structure to avoid constraint violations on existing rows
ALTER TABLE public.courses 
ADD COLUMN pedagogical_profile JSONB DEFAULT '{
  "target_learner_description": "",
  "expected_duration": "",
  "academic_level": "",
  "educational_context": ""
}'::jsonb NOT NULL;

-- Add check constraint to ensure keys always exist in the JSONB object
ALTER TABLE public.courses
ADD CONSTRAINT check_pedagogical_profile_keys 
CHECK (
    pedagogical_profile ? 'target_learner_description' AND
    pedagogical_profile ? 'expected_duration' AND
    pedagogical_profile ? 'academic_level' AND
    pedagogical_profile ? 'educational_context'
);

-- Add comment for AI introspection
COMMENT ON COLUMN public.courses.pedagogical_profile IS 'Almacena el ADN pedagógico del curso para optimización de agentes de IA (Discovery).';
