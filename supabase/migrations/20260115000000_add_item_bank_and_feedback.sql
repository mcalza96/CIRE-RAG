-- Migration: Add Item Bank and Feedback HTML
-- Description: Adds feedback_html to probe_options and creates item_bank table for semantic search.
-- Date: 2026-01-15

-- 1. Add feedback_html to probe_options
ALTER TABLE public.probe_options 
ADD COLUMN IF NOT EXISTS feedback_html TEXT;

-- 2. Create item_bank table
CREATE TABLE IF NOT EXISTS public.item_bank (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    competency_id UUID NOT NULL REFERENCES public.competency_nodes(id) ON DELETE CASCADE,
    content JSONB NOT NULL, -- Stores the full question structure (stem, options, etc.)
    embedding VECTOR(1536), -- For semantic search
    usage_count INT DEFAULT 0,
    success_rate FLOAT DEFAULT 0.0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb
);

-- 3. Enable RLS for item_bank
ALTER TABLE public.item_bank ENABLE ROW LEVEL SECURITY;

-- 4. RLS Policies for item_bank
DROP POLICY IF EXISTS "Item bank readable by everyone authenticated" ON public.item_bank;
CREATE POLICY "Item bank readable by everyone authenticated"
ON public.item_bank FOR SELECT
TO authenticated
USING (TRUE);

DROP POLICY IF EXISTS "Admins and instructors can manage item bank" ON public.item_bank;
CREATE POLICY "Admins and instructors can manage item bank"
ON public.item_bank FOR ALL
TO authenticated
USING (
    public.role_is('admin'::public.app_role) OR 
    public.role_is('instructor'::public.app_role)
);

-- 5. Indexes
CREATE INDEX IF NOT EXISTS item_bank_competency_id_idx ON public.item_bank(competency_id);
-- Index for vector search (ivfflat) - purely optional at this stage but good practice
-- CREATE INDEX ON item_bank USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
