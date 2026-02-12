-- =============================================================================
-- MIGRATION: UNIFIED TAXONOMY ONTOLOGY (Phase 1)
-- Description: Creates the hierarchical taxonomy system for CISRE.
--              Implements Materialized Path pattern for high-speed queries.
-- Date: 2026-04-01
-- =============================================================================

BEGIN;

-- 1. ENUMS
-- We drop and recreate to ensure all values ('root', etc.) are present
-- as IF NOT EXISTS won't update an existing type.
DROP TYPE IF EXISTS public.taxonomy_type CASCADE;
CREATE TYPE public.taxonomy_type AS ENUM ('root', 'context', 'level', 'subject', 'axis');

-- 2. TABLES

-- 2.1 taxonomy_nodes
CREATE TABLE IF NOT EXISTS public.taxonomy_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_id UUID REFERENCES public.taxonomy_nodes(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    type public.taxonomy_type NOT NULL,
    materialized_path TEXT NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    
    -- Ensure slug is unique per level
    UNIQUE(parent_id, slug)
);

-- 2.2 document_taxonomy (Many-to-Many)
-- Links source_documents to one or more taxonomy nodes.
CREATE TABLE IF NOT EXISTS public.document_taxonomy (
    document_id UUID NOT NULL REFERENCES public.source_documents(id) ON DELETE CASCADE,
    node_id UUID NOT NULL REFERENCES public.taxonomy_nodes(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (document_id, node_id)
);

-- 3. INDEXES
-- Index for materialized path queries (supports starts-with optimized searches)
CREATE INDEX IF NOT EXISTS idx_taxonomy_nodes_path ON public.taxonomy_nodes (materialized_path);
-- B-tree with text pattern ops for advanced prefix matching (LIKE 'path.%')
CREATE INDEX IF NOT EXISTS idx_taxonomy_nodes_path_ops ON public.taxonomy_nodes (materialized_path text_pattern_ops);

-- Index for many-to-many lookups
CREATE INDEX IF NOT EXISTS idx_document_taxonomy_node ON public.document_taxonomy(node_id);

-- 4. AUTOMATION (Triggers)

-- 4.1 Function to compute and propagate path
CREATE OR REPLACE FUNCTION public.update_taxonomy_path()
RETURNS TRIGGER AS $$
DECLARE
    parent_path TEXT;
BEGIN
    -- 1. Calculate the current node's path
    IF NEW.parent_id IS NULL THEN
        NEW.materialized_path := NEW.slug;
    ELSE
        SELECT materialized_path INTO parent_path 
        FROM public.taxonomy_nodes 
        WHERE id = NEW.parent_id;
        
        IF parent_path IS NULL THEN
            RAISE EXCEPTION 'Parent node % not found', NEW.parent_id;
        END IF;
        
        NEW.materialized_path := parent_path || '.' || NEW.slug;
    END IF;

    -- 2. If this is an UPDATE and the path changed, propagate to children
    IF (TG_OP = 'UPDATE') AND (OLD.materialized_path IS DISTINCT FROM NEW.materialized_path) THEN
        -- We handle propagation in an AFTER trigger to avoid recursion issues in the BEFORE stage
        -- But for simplicity and safety, we can also do it here if we are careful.
        -- Using an AFTER trigger is generally cleaner for propagation.
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 4.2 Before Trigger
CREATE TRIGGER trg_taxonomy_nodes_path_before
BEFORE INSERT OR UPDATE OF parent_id, slug
ON public.taxonomy_nodes
FOR EACH ROW
EXECUTE FUNCTION public.update_taxonomy_path();

-- 4.3 Function for Propagating Path Updates
CREATE OR REPLACE FUNCTION public.propagate_taxonomy_path()
RETURNS TRIGGER AS $$
BEGIN
    IF (OLD.materialized_path IS DISTINCT FROM NEW.materialized_path) THEN
        UPDATE public.taxonomy_nodes
        SET materialized_path = NEW.materialized_path || '.' || slug
        WHERE parent_id = NEW.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 4.4 After Trigger for Propagation
CREATE TRIGGER trg_taxonomy_nodes_path_after
AFTER UPDATE OF materialized_path
ON public.taxonomy_nodes
FOR EACH ROW
EXECUTE FUNCTION public.propagate_taxonomy_path();

-- 5. SECURITY (RLS)

-- Enable RLS
ALTER TABLE public.taxonomy_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.document_taxonomy ENABLE ROW LEVEL SECURITY;

-- Reading: Public (as requested)
DROP POLICY IF EXISTS "Public read access for taxonomy_nodes" ON public.taxonomy_nodes;
CREATE POLICY "Public read access for taxonomy_nodes"
ON public.taxonomy_nodes FOR SELECT
TO public
USING (true);

-- Writing: Only service_role or admins
DROP POLICY IF EXISTS "Admins can manage taxonomy_nodes" ON public.taxonomy_nodes;
CREATE POLICY "Admins can manage taxonomy_nodes"
ON public.taxonomy_nodes FOR ALL
TO authenticated
USING (public.is_admin())
WITH CHECK (public.is_admin());

-- Writing: service_role can do anything
DROP POLICY IF EXISTS "Service role can manage taxonomy_nodes" ON public.taxonomy_nodes;
CREATE POLICY "Service role can manage taxonomy_nodes"
ON public.taxonomy_nodes FOR ALL
TO service_role
USING (true)
WITH CHECK (true);

-- Document Taxonomy policies (tied to course ownership usually)
DROP POLICY IF EXISTS "Course owners can manage document entries" ON public.document_taxonomy;
CREATE POLICY "Course owners can manage document entries"
ON public.document_taxonomy FOR ALL
TO authenticated
USING (
    EXISTS (
        SELECT 1 FROM public.source_documents sd
        JOIN public.courses c ON c.id = sd.course_id
        WHERE sd.id = public.document_taxonomy.document_id
        AND (c.teacher_id = auth.uid() OR public.is_admin())
    )
)
WITH CHECK (
    EXISTS (
        SELECT 1 FROM public.source_documents sd
        JOIN public.courses c ON c.id = sd.course_id
        WHERE sd.id = public.document_taxonomy.document_id
        AND (c.teacher_id = auth.uid() OR public.is_admin())
    )
);

-- 6. SEED DATA (Removed)
-- Taxonomies are now managed via the Super Admin panel at /super-admin/taxonomies
-- No seed data is required - admin will create the taxonomy structure.

COMMIT;
