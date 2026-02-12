-- Migration: 20260312_save_fractal_structure_rpc
-- Context: RPC to atomically save the new Fractal Syllabus structure (Course -> Units -> Concepts).

-- Function to save a single fractal unit with its concepts
CREATE OR REPLACE FUNCTION save_fractal_unit(
  p_course_id UUID,
  p_title TEXT,
  p_description TEXT,
  p_order_index INTEGER,
  p_concepts JSONB -- Array of { title, description, complexity, order_index }
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER -- Runs with privileges of creator (to bypass strict RLS if needed, or just convenience)
AS $$
DECLARE
  v_unit_id UUID;
  v_concept JSONB;
BEGIN
  -- 1. Insert Unit
  INSERT INTO course_units (course_id, title, description, order_index)
  VALUES (p_course_id, p_title, p_description, p_order_index)
  RETURNING id INTO v_unit_id;

  -- 2. Insert Concepts
  IF p_concepts IS NOT NULL AND jsonb_array_length(p_concepts) > 0 THEN
    INSERT INTO unit_concepts (unit_id, title, description, complexity, order_index)
    SELECT
      v_unit_id,
      (c->>'title')::text,
      (c->>'description')::text,
      COALESCE((c->>'complexity')::text, 'basic'),
      COALESCE((c->>'order_index')::int, 0)
    FROM jsonb_array_elements(p_concepts) as c;
  END IF;

  RETURN v_unit_id;
END;
$$;
