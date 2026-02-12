-- =============================================================================
-- MIGRATION: SCHEMA UNIFICATION (Phase 5)
-- Description: Unifies 'taxonomy_nodes' and legacy 'taxonomies' into a 
--              single source of truth table: 'taxonomies'.
-- Date: 2026-04-05
-- IDEMPOTENT: Safe to run multiple times.
-- =============================================================================

BEGIN;

-- 1. CONDITIONAL RENAME & UPGRADE
-- Only rename if taxonomy_nodes exists and taxonomies doesn't
DO $$
BEGIN
    -- Rename taxonomy_nodes to taxonomies if taxonomy_nodes exists
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'taxonomy_nodes') THEN
        DROP TABLE IF EXISTS public.taxonomies CASCADE; -- Force drop legacy if we have fresh nodes
        ALTER TABLE public.taxonomy_nodes RENAME TO taxonomies;
    END IF;
END $$;

-- 2. ENSURE SCHEMA INTEGRITY (Upgrade Legacy or Verify New)
-- This block ensures 'taxonomies' has all required columns even if it came from legacy
ALTER TABLE public.taxonomies 
    ADD COLUMN IF NOT EXISTS code TEXT,
    ADD COLUMN IF NOT EXISTS order_index INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT true,
    ADD COLUMN IF NOT EXISTS slug TEXT,
    ADD COLUMN IF NOT EXISTS materialized_path TEXT,
    ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS parent_id UUID REFERENCES public.taxonomies(id) ON DELETE CASCADE;

-- 2.1 Handle 'type' column with Enum
DO $$ 
BEGIN 
    -- Ensure Enum Exists
    IF NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid = e.enumtypid WHERE t.typname = 'taxonomy_type' AND e.enumlabel = 'document_type') THEN
        ALTER TYPE public.taxonomy_type ADD VALUE IF NOT EXISTS 'document_type';
    END IF;

    -- Add 'type' column if missing
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'taxonomies' AND column_name = 'type') THEN
        ALTER TABLE public.taxonomies ADD COLUMN type public.taxonomy_type DEFAULT 'context'::public.taxonomy_type;
    END IF;
END $$;

-- 3. UPDATE ENUMS
DO $$ 
BEGIN 
    IF NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_enum e ON t.oid = e.enumtypid WHERE t.typname = 'taxonomy_type' AND e.enumlabel = 'document_type') THEN
        ALTER TYPE public.taxonomy_type ADD VALUE 'document_type';
    END IF;
END $$;

-- 4. UPDATE TRIGGER FUNCTIONS (CREATE OR REPLACE is idempotent)
CREATE OR REPLACE FUNCTION public.update_taxonomy_path()
RETURNS TRIGGER AS $$
DECLARE
    parent_path TEXT;
BEGIN
    IF NEW.parent_id IS NULL THEN
        NEW.materialized_path := NEW.slug;
    ELSE
        SELECT materialized_path INTO parent_path 
        FROM public.taxonomies 
        WHERE id = NEW.parent_id;
        
        IF parent_path IS NULL THEN
            RAISE EXCEPTION 'Parent node % not found', NEW.parent_id;
        END IF;
        
        NEW.materialized_path := parent_path || '.' || NEW.slug;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION public.propagate_taxonomy_path()
RETURNS TRIGGER AS $$
BEGIN
    IF (OLD.materialized_path IS DISTINCT FROM NEW.materialized_path) THEN
        UPDATE public.taxonomies
        SET materialized_path = NEW.materialized_path || '.' || slug
        WHERE parent_id = NEW.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 5. CONDITIONAL TRIGGER RENAME (using DO block for safety)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_taxonomy_nodes_path_before') THEN
        ALTER TRIGGER trg_taxonomy_nodes_path_before ON public.taxonomies RENAME TO trg_taxonomies_path_before;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_taxonomy_nodes_path_after') THEN
        ALTER TRIGGER trg_taxonomy_nodes_path_after ON public.taxonomies RENAME TO trg_taxonomies_path_after;
    END IF;
END $$;

-- 6. CONDITIONAL INDEX RENAME
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_taxonomy_nodes_path') THEN
        ALTER INDEX idx_taxonomy_nodes_path RENAME TO idx_taxonomies_path;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_taxonomy_nodes_path_ops') THEN
        ALTER INDEX idx_taxonomy_nodes_path_ops RENAME TO idx_taxonomies_path_ops;
    END IF;
END $$;

-- 7. UPDATE RLS POLICIES (idempotent with DROP IF EXISTS)
DROP POLICY IF EXISTS "Public read access for taxonomy_nodes" ON public.taxonomies;
DROP POLICY IF EXISTS "Admins can manage taxonomy_nodes" ON public.taxonomies;
DROP POLICY IF EXISTS "Service role can manage taxonomy_nodes" ON public.taxonomies;
DROP POLICY IF EXISTS "Public read access for taxonomies" ON public.taxonomies;
DROP POLICY IF EXISTS "Admins can manage taxonomies" ON public.taxonomies;
DROP POLICY IF EXISTS "Service role can manage taxonomies" ON public.taxonomies;

CREATE POLICY "Public read access for taxonomies" ON public.taxonomies FOR SELECT TO public USING (true);
CREATE POLICY "Admins can manage taxonomies" ON public.taxonomies FOR ALL TO authenticated USING (public.is_admin()) WITH CHECK (public.is_admin());
CREATE POLICY "Service role can manage taxonomies" ON public.taxonomies FOR ALL TO service_role USING (true) WITH CHECK (true);

COMMIT;
