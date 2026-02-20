-- Migration: Cleanup Legacy Retrieval RPCs
-- Description: Removes deprecated retrieval functions identified in code review to reduce technical debt.

DROP FUNCTION IF EXISTS public.search_course_knowledge(text, vector, float, int, uuid);
DROP FUNCTION IF EXISTS public.match_vectors_hybrid(vector, float, int, uuid, uuid, text, text);
