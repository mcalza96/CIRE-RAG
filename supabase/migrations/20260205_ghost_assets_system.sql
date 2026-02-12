-- =============================================================================
-- MIGRATION: GHOST ASSETS SYSTEM (Virtual Pointers)
-- =============================================================================
-- Description: Separates file ownership from knowledge availability.
--              Introduces a global catalog for "injected" knowledge assets.
--
-- Architecture:
--   - global_assets: Metadata for documents loaded via CLI (e.g. Curriculums).
--   - context_subscriptions: Links courses to these assets, enabling AI access.
-- =============================================================================

BEGIN;

-- 1. TYPES/ENUMS
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'asset_content_type') THEN
        CREATE TYPE public.asset_content_type AS ENUM ('hard_constraint', 'soft_knowledge');
    END IF;
END $$;

-- 2. TABLE: global_assets (The Library Catalog)
CREATE TABLE IF NOT EXISTS public.global_assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    description TEXT,
    category TEXT NOT NULL,
    tags TEXT[] DEFAULT ARRAY[]::TEXT[],
    
    -- Link to the vector database collection/source
    vector_source_id TEXT NOT NULL,
    
    -- Pedagogical behavior indicator
    content_type public.asset_content_type DEFAULT 'soft_knowledge'::public.asset_content_type NOT NULL,
    
    -- Visual and UI properties
    visual_metadata JSONB DEFAULT '{}'::jsonb,
    
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

COMMENT ON TABLE public.global_assets IS 'Master catalog of knowledge assets loaded via CLI, available for reference across the platform.';
COMMENT ON COLUMN public.global_assets.vector_source_id IS 'ID linking to the external or internal vector store collection.';
COMMENT ON COLUMN public.global_assets.content_type IS 'Determines if the RAG should treat this as a strict rule (hard_constraint) or general context (soft_knowledge).';

-- 3. TABLE: context_subscriptions (The Loan Card)
CREATE TABLE IF NOT EXISTS public.context_subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Foreign Keys
    course_id UUID NOT NULL REFERENCES public.courses(id) ON DELETE CASCADE,
    global_asset_id UUID NOT NULL REFERENCES public.global_assets(id) ON DELETE CASCADE,
    
    -- Subscription State
    is_active BOOLEAN DEFAULT TRUE NOT NULL,
    
    -- Personalization (Prompt weights, exclusion rules, etc.)
    settings JSONB DEFAULT '{}'::jsonb,
    
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    
    -- Restriction: Prevent duplicate subscriptions
    CONSTRAINT uq_course_asset_subscription UNIQUE (course_id, global_asset_id)
);

COMMENT ON TABLE public.context_subscriptions IS 'Bridges courses with global assets, allowing teachers to reference knowledge without ownership.';

-- 4. INDEXES
CREATE INDEX IF NOT EXISTS idx_global_assets_category ON public.global_assets(category);
CREATE INDEX IF NOT EXISTS idx_global_assets_tags ON public.global_assets USING gin(tags);
CREATE INDEX IF NOT EXISTS idx_context_subs_course_id ON public.context_subscriptions(course_id);
CREATE INDEX IF NOT EXISTS idx_context_subs_asset_id ON public.context_subscriptions(global_asset_id);

-- 5. ROW LEVEL SECURITY (RLS)
ALTER TABLE public.global_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.context_subscriptions ENABLE ROW LEVEL SECURITY;

-- 5.1 Policies for global_assets
-- Visible to all authenticated users (SELECT), only manageable by service_role/admin
CREATE POLICY "global_assets_read_all" ON public.global_assets
    FOR SELECT TO authenticated
    USING (TRUE);

CREATE POLICY "global_assets_admin_all" ON public.global_assets
    FOR ALL TO service_role
    USING (TRUE)
    WITH CHECK (TRUE);

-- 5.2 Policies for context_subscriptions
-- Teachers can manage subscriptions for their own courses
CREATE POLICY "subs_teacher_manage" ON public.context_subscriptions
    FOR ALL TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.courses c
            WHERE c.id = public.context_subscriptions.course_id
            AND c.teacher_id = auth.uid()
        )
        OR public.is_admin()
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM public.courses c
            WHERE c.id = public.context_subscriptions.course_id
            AND c.teacher_id = auth.uid()
        )
        OR public.is_admin()
    );

-- 6. TIMESTAMPS TRIGGER
CREATE OR REPLACE FUNCTION public.update_ghost_asset_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_global_assets_updated
    BEFORE UPDATE ON public.global_assets
    FOR EACH ROW EXECUTE FUNCTION public.update_ghost_asset_timestamp();

CREATE TRIGGER trg_context_subscriptions_updated
    BEFORE UPDATE ON public.context_subscriptions
    FOR EACH ROW EXECUTE FUNCTION public.update_ghost_asset_timestamp();

COMMIT;
