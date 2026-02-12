-- =============================================================================
-- MIGRATION: RUBRICS SCHEMA (Normative Validation System)
-- Description: Adds tables for structured educational rubrics with vector search.
--              Includes a hierarchical model (Rubric -> Strand -> Criteria) and
--              semantic descriptors with embeddings.
-- Date: 2026-02-05
-- =============================================================================

BEGIN;

-- 1. TABLES

-- Cleanup old empty tables if they exist with wrong schema (Schema Drift)
DO $$ 
BEGIN
    -- Check if rubric_criteria exists and is missing strand_id (old schema had 'title' instead of 'name' and no strand_id)
    IF EXISTS (SELECT FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'rubric_criteria') THEN
        IF NOT EXISTS (SELECT FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'rubric_criteria' AND column_name = 'strand_id') THEN
            DROP TABLE public.rubric_criteria CASCADE;
        END IF;
    END IF;

    -- Check if rubrics exists and has teacher_id (old schema) instead of authority/year
    IF EXISTS (SELECT FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'rubrics') THEN
        IF EXISTS (SELECT FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'rubrics' AND column_name = 'teacher_id') THEN
            DROP TABLE public.rubrics CASCADE;
        END IF;
    END IF;
END $$;

-- 1.1 rubrics (Master Table)
-- Stores metadata about the official rubric documents.
CREATE TABLE IF NOT EXISTS public.rubrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    description TEXT,
    authority TEXT, -- e.g., "Ministerio de Educación"
    year INTEGER,
    education_level TEXT,
    is_system BOOLEAN DEFAULT true, -- true = official, readonly for teachers
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- 1.2 rubric_strands (Dimensions/Domains)
-- Structural groupings (e.g., "Tarea 1: Planificación")
CREATE TABLE IF NOT EXISTS public.rubric_strands (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rubric_id UUID NOT NULL REFERENCES public.rubrics(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    order_index INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Index for FK
CREATE INDEX IF NOT EXISTS idx_rubric_strands_rubric ON public.rubric_strands(rubric_id);

-- 1.3 rubric_criteria (Rows/Indicators)
-- Specific items being evaluated (e.g., "Formulación de objetivos")
CREATE TABLE IF NOT EXISTS public.rubric_criteria (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rubric_id UUID NOT NULL REFERENCES public.rubrics(id) ON DELETE CASCADE, -- Shortcut for queries
    strand_id UUID NOT NULL REFERENCES public.rubric_strands(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    order_index INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Indexes for FKs
CREATE INDEX IF NOT EXISTS idx_rubric_criteria_rubric ON public.rubric_criteria(rubric_id);
CREATE INDEX IF NOT EXISTS idx_rubric_criteria_strand ON public.rubric_criteria(strand_id);

-- 1.4 rubric_levels (Columns/Scales)
-- Performance levels (e.g., "Insatisfactorio", "Competente")
CREATE TABLE IF NOT EXISTS public.rubric_levels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rubric_id UUID NOT NULL REFERENCES public.rubrics(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    score_value INTEGER, -- Optional numeric value
    order_index INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Index for FK
CREATE INDEX IF NOT EXISTS idx_rubric_levels_rubric ON public.rubric_levels(rubric_id);

-- 1.5 rubric_descriptors (Cells/Vectors)
-- The actual normative text and its embedding.
CREATE TABLE IF NOT EXISTS public.rubric_descriptors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    criteria_id UUID NOT NULL REFERENCES public.rubric_criteria(id) ON DELETE CASCADE,
    level_id UUID NOT NULL REFERENCES public.rubric_levels(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    embedding vector(1536), -- Vector for semantic search
    keywords TEXT[], -- For hybrid filtering
    fts tsvector GENERATED ALWAYS AS (to_tsvector('spanish', description)) STORED,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(criteria_id, level_id) -- Ensure one descriptor per cell
);

-- Indexes for FKs
CREATE INDEX IF NOT EXISTS idx_rubric_descriptors_criteria ON public.rubric_descriptors(criteria_id);
CREATE INDEX IF NOT EXISTS idx_rubric_descriptors_level ON public.rubric_descriptors(level_id);

-- HNSW Index for Vector Search
CREATE INDEX IF NOT EXISTS idx_rubric_descriptors_embedding 
ON public.rubric_descriptors USING hnsw (embedding vector_cosine_ops);

-- GIN Index for Full Text Search
CREATE INDEX IF NOT EXISTS idx_rubric_descriptors_fts 
ON public.rubric_descriptors USING gin (fts);


-- 1.6 course_rubrics (Association)
-- Links a course to a rubric (Pivot table).
CREATE TABLE IF NOT EXISTS public.course_rubrics (
    course_id UUID NOT NULL REFERENCES public.courses(id) ON DELETE CASCADE,
    rubric_id UUID NOT NULL REFERENCES public.rubrics(id) ON DELETE CASCADE,
    assigned_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (course_id, rubric_id)
);

-- Index for FK
CREATE INDEX IF NOT EXISTS idx_course_rubrics_course ON public.course_rubrics(course_id);

-- 2. SECURITY (RLS)

-- Enable RLS on all tables
ALTER TABLE public.rubrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rubric_strands ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rubric_criteria ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rubric_levels ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rubric_descriptors ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.course_rubrics ENABLE ROW LEVEL SECURITY;

-- 2.1 Policies for Master Data (rubrics, strands, criteria, levels, descriptors)
-- Readable by all authenticated users (teachers).
-- Writeable ONLY by admins.

-- Read Policy (Global)
CREATE POLICY "Authenticated users can view rubrics" ON public.rubrics FOR SELECT TO authenticated USING (true);
CREATE POLICY "Authenticated users can view strands" ON public.rubric_strands FOR SELECT TO authenticated USING (true);
CREATE POLICY "Authenticated users can view criteria" ON public.rubric_criteria FOR SELECT TO authenticated USING (true);
CREATE POLICY "Authenticated users can view levels" ON public.rubric_levels FOR SELECT TO authenticated USING (true);
CREATE POLICY "Authenticated users can view descriptors" ON public.rubric_descriptors FOR SELECT TO authenticated USING (true);

-- Write Policy (Admins Only)
CREATE POLICY "Admins can manage rubrics" ON public.rubrics FOR ALL TO authenticated USING (public.is_admin()) WITH CHECK (public.is_admin());
CREATE POLICY "Admins can manage strands" ON public.rubric_strands FOR ALL TO authenticated USING (public.is_admin()) WITH CHECK (public.is_admin());
CREATE POLICY "Admins can manage criteria" ON public.rubric_criteria FOR ALL TO authenticated USING (public.is_admin()) WITH CHECK (public.is_admin());
CREATE POLICY "Admins can manage levels" ON public.rubric_levels FOR ALL TO authenticated USING (public.is_admin()) WITH CHECK (public.is_admin());
CREATE POLICY "Admins can manage descriptors" ON public.rubric_descriptors FOR ALL TO authenticated USING (public.is_admin()) WITH CHECK (public.is_admin());


-- 2.2 Policies for Course Associations (course_rubrics)
-- Teachers can only manage associations for their own courses.

CREATE POLICY "Teachers can manage their course rubrics"
ON public.course_rubrics
FOR ALL
TO authenticated
USING (
    EXISTS (
        SELECT 1 FROM public.courses c
        WHERE c.id = public.course_rubrics.course_id
        AND (c.teacher_id = auth.uid() OR public.is_admin())
    )
)
WITH CHECK (
    EXISTS (
        SELECT 1 FROM public.courses c
        WHERE c.id = public.course_rubrics.course_id
        AND (c.teacher_id = auth.uid() OR public.is_admin())
    )
);

COMMIT;
