-- Migration: Drop Legacy Tables
-- Description: Removes tables that have been replaced by the Unified Node Architecture (course_nodes).
-- Verification: Ensure all code has been refactored to use course_nodes before applying.

-- 1. Drop Dependencies (Views)
DROP VIEW IF EXISTS vw_syllabus_progress;

-- 2. Drop Legacy Chunks and Search Functions
DROP TABLE IF EXISTS public.content_chunks CASCADE;
DROP FUNCTION IF EXISTS public.hybrid_search(TEXT, VECTOR, FLOAT, INT, UUID);
DROP FUNCTION IF EXISTS public.hybrid_search_document(TEXT, VECTOR, FLOAT, INT, UUID);

-- 3. Drop Tables (Order matters due to Foreign Keys)

-- knowledge_atoms depends on unit_concepts
DROP TABLE IF EXISTS knowledge_atoms;

-- unit_concepts depends on course_units
DROP TABLE IF EXISTS unit_concepts;

-- course_syllabus depends on competency_nodes, lessons, courses
DROP TABLE IF EXISTS course_syllabus;

-- competency_edges depends on competency_nodes
DROP TABLE IF EXISTS competency_edges;

-- competency_nodes depends on self
DROP TABLE IF EXISTS competency_nodes CASCADE;

-- course_units depends on courses (Clean up last)
DROP TABLE IF EXISTS course_units CASCADE;

-- 2. Optional: Clean up unit_id references in lessons if any?
-- (Check if lessons table needs cleanup, usually lessons are separate entities, but course_syllabus linked them).
-- Leaving lessons table intact as it might be used for other things.

-- 3. Verification Query
-- SELECT relname FROM pg_class WHERE relname IN ('course_units', 'unit_concepts', 'knowledge_atoms', 'course_syllabus', 'competency_nodes');
-- Should return 0 rows.
