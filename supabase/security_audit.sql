-- Security Audit Script for CISRE
-- This script attempts to violate RLS policies to verify they are working.

-- 1. Setup: Create a test user (Attacker)
INSERT INTO auth.users (id, email)
VALUES ('00000000-0000-0000-0000-000000000000', 'attacker@test.com')
ON CONFLICT DO NOTHING;

-- 2. Setup: Create a specific victim resource (e.g., a Course)
-- Assumes a 'courses' table exists and has a 'teacher_id' column
-- We'll try to read it as the attacker.

-- Switch to Attacker Identity
SET ROLE authenticated;
SET request.jwt.claim.sub = '00000000-0000-0000-0000-000000000000';

-- 3. Attack: Attempt to list courses (Should only see their own or public ones)
SELECT * FROM public.courses;

-- 4. Attack: Attempt to read diagnostic probes (Should be restricted)
SELECT * FROM public.diagnostic_probes;

-- 5. Attack: Attempt to insert a malicious node
INSERT INTO public.path_nodes (learner_id, content_id)
VALUES ('victim-learner-id', 'malicious-content-id');

-- Reset Role
RESET ROLE;
