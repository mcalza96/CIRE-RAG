-- Migration: Atomic Mutation RPC
-- Description: Adds a safe function to insert nodes into the exam queue using atomic jsonb_insert
-- Date: 2026-01-16

CREATE OR REPLACE FUNCTION public.insert_exam_attempt_node(
    p_attempt_id UUID,
    p_index INT,
    p_new_node JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_updated_queue JSONB;
    v_current_queue JSONB;
BEGIN
    -- 1. Lock the row for update to prevent race conditions
    SELECT questions_queue INTO v_current_queue
    FROM public.exam_attempts
    WHERE id = p_attempt_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Exam attempt not found';
    END IF;

    -- 2. Validate index (basic bounds check)
    -- Allow appending at the end (index = length)
    IF p_index < 0 OR p_index > jsonb_array_length(v_current_queue) THEN
        RAISE EXCEPTION 'Index out of bounds';
    END IF;

    -- 3. Perform atomic insertion using native PostGIS/JSONB functions if available,
    -- or standard jsonb_insert. Note: jsonb_insert path format is '{index}'
    
    UPDATE public.exam_attempts
    SET 
        questions_queue = jsonb_insert(questions_queue, ARRAY[p_index::text], p_new_node),
        updated_at = NOW()
    WHERE id = p_attempt_id
    RETURNING questions_queue INTO v_updated_queue;

    RETURN v_updated_queue;
END;
$$;

-- Grant execute permissions
GRANT EXECUTE ON FUNCTION public.insert_exam_attempt_node(UUID, INT, JSONB) TO authenticated;
GRANT EXECUTE ON FUNCTION public.insert_exam_attempt_node(UUID, INT, JSONB) TO service_role;
