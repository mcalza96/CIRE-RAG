-- Add applied_mutations JSONB column to exam_attempts for forensic auditing
ALTER TABLE exam_attempts
ADD COLUMN applied_mutations JSONB DEFAULT '[]'::jsonb;

COMMENT ON COLUMN exam_attempts.applied_mutations IS 'Log of all path mutations applied during this attempt (forensic ledger)';
