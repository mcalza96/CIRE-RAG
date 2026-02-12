-- Migration: Fog of War - Visible Sectors RPC
-- Purpose: Efficiently retrieve only visible knowledge sectors for a student (White Box -> Fog of War)

-- 1. Create the RPC function
CREATE OR REPLACE FUNCTION public.get_student_visible_sectors(
    p_student_id UUID,
    p_course_id UUID
)
RETURNS TABLE (
    id UUID,
    name TEXT,
    unit_id UUID,
    status TEXT, -- 'LOCKED', 'HEALTHY', 'CORRUPTED', 'CRITICAL'
    last_updated TIMESTAMPTZ,
    forensics JSONB,
    metrics JSONB
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    -- Check if lessons table has 'status' column dynamically to avoid breaking if schema differs
    v_has_status_col BOOLEAN;
BEGIN
    -- Check for column existence in information_schema (optimization to prevent runtime error)
    -- However, inside plpgsql we can just query. 
    -- We assume the schema strictly follows the 'published' requirement.
    
    RETURN QUERY
    WITH 
    -- 1. All competencies visible via Syllabus (Units)
    syllabus_visible AS (
        SELECT 
            cs.competency_id,
            cs.unit_id
        FROM public.course_syllabus cs
        JOIN public.lessons l ON l.id = cs.unit_id
        WHERE cs.course_id = p_course_id
        -- We filter by status if it exists, otherwise assume visible (or handle via app logic)
        -- Since user explicitly asked for 'published', we include the check.
        -- If this fails due to missing column, the migration implies a dependency.
        AND (l.status = 'PUBLISHED' OR l.status IS NULL) 
    ),
    -- 2. All competencies visible via Active Assignments (Assignments)
    assignment_visible AS (
        SELECT DISTINCT
            (item->>'competencyId')::UUID as competency_id,
            NULL::UUID as unit_id
        FROM public.exam_assignments ea
        JOIN public.exams e ON e.id = ea.exam_id
        CROSS JOIN LATERAL jsonb_array_elements(e.q_matrix) as item
        WHERE ea.student_id = p_student_id
        AND e.status = 'PUBLISHED'
        AND item->>'competencyId' IS NOT NULL
        -- Guard Clause: Ensure it's a valid UUID
        AND item->>'competencyId' ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
        -- Ideally we should filter exams by course, but if the student is assigned, they should see it.
    ),
    -- Combine Sources
    all_visible AS (
        SELECT sv.competency_id, sv.unit_id 
        FROM syllabus_visible sv
        UNION
        SELECT av_src.competency_id, av_src.unit_id 
        FROM assignment_visible av_src
    ),
    -- 3. Student Forensic State (From Evidence)
    student_state AS (
        SELECT DISTINCT ON ((diagnosis->>'competencyId')::UUID)
            (diagnosis->>'competencyId')::UUID as competency_id,
            diagnosis->>'state' as state,
            COALESCE((diagnosis->'evidence'->>'confidenceScore')::float, 0) as confidence_score,
            ea.finished_at
        FROM public.exam_attempts ea
        CROSS JOIN LATERAL jsonb_array_elements(ea.results_cache->'competencyDiagnoses') as diagnosis
        WHERE ea.learner_id = p_student_id
        AND ea.status = 'COMPLETED'
        -- Guard Clause: Ensure it's a valid UUID
        AND diagnosis->>'competencyId' ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
        ORDER BY (diagnosis->>'competencyId')::UUID, ea.finished_at DESC
    ),
    -- 4. Shadow Nodes (Direct Detection in Metrics)
    shadow_detections AS (
         SELECT ss_shadow.competency_id 
         FROM student_state ss_shadow
         WHERE ss_shadow.state = 'MISCONCEPTION'
    )
    
    SELECT 
        cn.id,
        cn.title as name,
        -- Try to resolve unit_id from syllabus if missing from assignment source
        COALESCE(av.unit_id, (
            SELECT cs.unit_id 
            FROM public.course_syllabus cs 
            WHERE cs.competency_id = cn.id 
            AND cs.course_id = p_course_id 
            LIMIT 1
        )) as unit_id,
        CASE
            WHEN sd.competency_id IS NOT NULL THEN 'CRITICAL'
            WHEN ss.state = 'MASTERED' THEN 'HEALTHY'
            WHEN ss.confidence_score >= 80 THEN 'HEALTHY' -- Fallback if state logic varies
            WHEN ss.competency_id IS NOT NULL AND ss.confidence_score < 70 THEN 'CORRUPTED'
            WHEN ss.competency_id IS NOT NULL THEN 'CORRUPTED' -- Default if evaluated but not mastered/critical
            ELSE 'LOCKED'
        END as status,
        ss.finished_at as last_updated,
        jsonb_build_object(
            'shadowNodeDetected', (sd.competency_id IS NOT NULL),
            'shadowNodeName', CASE WHEN sd.competency_id IS NOT NULL THEN 'Detected Misconception' ELSE NULL END,
            'evidenceSnippet', CASE WHEN sd.competency_id IS NOT NULL THEN 'Forensic Evidence Logged' ELSE NULL END
        ) as forensics,
        jsonb_build_object(
            'competencyLevel', COALESCE(ss.confidence_score, 0) / 100.0,
            'confidenceLevel', 0.9 -- Placeholder
        ) as metrics
        
    FROM all_visible av
    JOIN public.competency_nodes cn ON cn.id = av.competency_id
    LEFT JOIN student_state ss ON ss.competency_id = cn.id
    LEFT JOIN shadow_detections sd ON sd.competency_id = cn.id;
    
END;
$$;
