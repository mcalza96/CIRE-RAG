-- =============================================================================
-- MIGRATION: RUBRIC RETRIEVAL RPC
-- Description: Creates a search function for rubric descriptors with full context.
--              Enforces course-level isolation.
-- Date: 2026-02-10
-- =============================================================================

BEGIN;

CREATE OR REPLACE FUNCTION public.search_rubric_descriptors(
    query_embedding vector(1536),
    match_threshold FLOAT,
    match_count INT,
    filter_course_id UUID
)
RETURNS TABLE (
    id UUID,
    descriptor_text TEXT,
    criteria_name TEXT,
    level_name TEXT,
    rubric_title TEXT,
    similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT 
        rd.id,
        rd.description as descriptor_text,
        rc.name as criteria_name,
        rl.name as level_name,
        r.title as rubric_title,
        (1 - (rd.embedding <=> query_embedding)) AS similarity
    FROM public.rubric_descriptors rd
    JOIN public.rubric_criteria rc ON rc.id = rd.criteria_id
    JOIN public.rubric_levels rl ON rl.id = rd.level_id
    JOIN public.rubrics r ON r.id = rc.rubric_id
    JOIN public.course_rubrics cr ON cr.rubric_id = r.id
    WHERE cr.course_id = filter_course_id
    AND (1 - (rd.embedding <=> query_embedding)) > match_threshold
    ORDER BY similarity DESC
    LIMIT match_count;
END;
$$ SECURITY DEFINER SET search_path = public, extensions;

-- Grant access
GRANT EXECUTE ON FUNCTION public.search_rubric_descriptors(vector(1536), FLOAT, INT, UUID) TO authenticated;
GRANT EXECUTE ON FUNCTION public.search_rubric_descriptors(vector(1536), FLOAT, INT, UUID) TO service_role;

COMMIT;
