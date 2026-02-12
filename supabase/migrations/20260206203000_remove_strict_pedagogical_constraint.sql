-- Migration: Remove strict pedagogical profile key check
-- Reason: The application schema for 'pedagogical_profile' has evolved to a nested structure (PedagogicalDNA),
-- but this constraint enforces the old flat structure, causing INSERT failures.
-- We drop the constraint to allow the new structure.
-- We do NOT add a new constraint yet to preserve compatibility with existing rows that use the old structure.

ALTER TABLE public.courses
DROP CONSTRAINT IF EXISTS check_pedagogical_profile_keys;


