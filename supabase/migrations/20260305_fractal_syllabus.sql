-- Migration: 20260305_fractal_syllabus
-- Context: Fractal Shift (Course -> Unit -> Concept -> Atom)

-- 1. Create or Update course_units
-- The user specified "Actualización o Creación"
CREATE TABLE IF NOT EXISTS public.course_units (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id uuid NOT NULL REFERENCES public.courses(id) ON DELETE CASCADE,
    title text NOT NULL,
    description text,
    order_index integer DEFAULT 0,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);

-- Enable RLS for course_units if newly created
ALTER TABLE course_units ENABLE ROW LEVEL SECURITY;

-- Basic Policy for course_units (Teacher and Students)
DO $$ 
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'course_units' AND policyname = 'view_units_if_course_visible') THEN
        CREATE POLICY "view_units_if_course_visible" ON course_units
        FOR SELECT
        USING (
            EXISTS (
                SELECT 1 FROM courses c
                WHERE c.id = course_units.course_id
                AND (
                    c.teacher_id = auth.uid()
                    OR
                    EXISTS (
                        SELECT 1 FROM cohort_members cm
                        JOIN cohorts co ON co.id = cm.cohort_id
                        WHERE co.course_id = c.id AND cm.student_id = auth.uid()
                    )
                )
            )
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'course_units' AND policyname = 'edit_units_if_course_owner') THEN
        CREATE POLICY "edit_units_if_course_owner" ON course_units
        FOR ALL
        USING (
            EXISTS (
                SELECT 1 FROM courses c
                WHERE c.id = course_units.course_id
                AND c.teacher_id = auth.uid()
            )
        );
    END IF;
END $$;

-- 2. Create unit_concepts table
CREATE TABLE IF NOT EXISTS unit_concepts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    unit_id uuid NOT NULL REFERENCES course_units(id) ON DELETE CASCADE,
    title text NOT NULL,
    description text, -- Optional context for AI
    order_index integer NOT NULL DEFAULT 0,
    complexity text CHECK (complexity IN ('basic', 'intermediate', 'advanced')) DEFAULT 'basic',
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);

-- 3. Create knowledge_atoms table
CREATE TABLE IF NOT EXISTS knowledge_atoms (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    concept_id uuid NOT NULL REFERENCES unit_concepts(id) ON DELETE CASCADE,
    content text NOT NULL, -- The "Atomic" truth
    type text CHECK (type IN ('definition', 'formula', 'process', 'example', 'historical_fact')) DEFAULT 'definition',
    source_chunk_ids uuid[] DEFAULT '{}', -- Array of FKs to content_chunks (Loose relationship for RAG)
    rag_confidence float DEFAULT 0.0,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);

-- 4. Enable Row Level Security (RLS)
ALTER TABLE unit_concepts ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_atoms ENABLE ROW LEVEL SECURITY;

-- 5. RLS Policies
-- Helper function to check course ownership from a unit_id
-- We assume a chain: unit -> course -> teacher_id (owner)
-- Note: course_units usually has course_id. courses has owner_id (or similar auth check).
-- Ideally we reuse existing patterns. 
-- For simplicity and performance in RLS, we often do a direct EXISTS check.

-- Policy: unit_concepts (View)
CREATE POLICY "view_concepts_if_course_visible" ON unit_concepts
FOR SELECT
USING (
    EXISTS (
        SELECT 1 FROM course_units cu
        JOIN courses c ON c.id = cu.course_id
        WHERE cu.id = unit_concepts.unit_id
        AND (
            c.teacher_id = auth.uid() -- Teacher own data
            OR
            EXISTS ( -- Student enrollment check logic if needed, usually open for enrolled
                SELECT 1 FROM cohort_members cm
                JOIN cohorts co ON co.id = cm.cohort_id
                WHERE co.course_id = c.id AND cm.student_id = auth.uid()
            )
        )
    )
);

-- Policy: unit_concepts (Edit - Teacher Only)
CREATE POLICY "edit_concepts_if_course_owner" ON unit_concepts
FOR ALL
USING (
    EXISTS (
        SELECT 1 FROM course_units cu
        JOIN courses c ON c.id = cu.course_id
        WHERE cu.id = unit_concepts.unit_id
        AND c.teacher_id = auth.uid()
    )
);

-- Policy: knowledge_atoms (View)
CREATE POLICY "view_atoms_if_course_visible" ON knowledge_atoms
FOR SELECT
USING (
    EXISTS (
        SELECT 1 FROM unit_concepts uc
        JOIN course_units cu ON cu.id = uc.unit_id
        JOIN courses c ON c.id = cu.course_id
        WHERE uc.id = knowledge_atoms.concept_id
        AND (
            c.teacher_id = auth.uid()
            OR
            EXISTS (
                SELECT 1 FROM cohort_members cm
                JOIN cohorts co ON co.id = cm.cohort_id
                WHERE co.course_id = c.id AND cm.student_id = auth.uid()
            )
        )
    )
);

-- Policy: knowledge_atoms (Edit - Teacher Only)
CREATE POLICY "edit_atoms_if_course_owner" ON knowledge_atoms
FOR ALL
USING (
    EXISTS (
        SELECT 1 FROM unit_concepts uc
        JOIN course_units cu ON cu.id = uc.unit_id
        JOIN courses c ON c.id = cu.course_id
        WHERE uc.id = knowledge_atoms.concept_id
        AND c.teacher_id = auth.uid()
    )
);

-- 6. Indexes for performance
CREATE INDEX IF NOT EXISTS idx_unit_concepts_unit_id ON unit_concepts(unit_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_atoms_concept_id ON knowledge_atoms(concept_id);
