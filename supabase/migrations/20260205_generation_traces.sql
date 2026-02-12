-- =============================================================================
-- Migration: Generation Traces for Forensic Auditing
-- =============================================================================
-- Purpose: Store traces of LLM generations with citation metadata for:
--   1. Auditing: Why was this decision made?
--   2. Compliance: What rules were applied?
--   3. Evolution: Track behavior changes over time
-- =============================================================================

-- Enable UUID extension if not exists
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =============================================================================
-- TABLE: generation_traces
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.generation_traces (
    -- Primary Key
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Tenant isolation
    tenant_id UUID NOT NULL,
    
    -- Session tracking (optional)
    session_id UUID,
    
    -- User who triggered generation (optional)
    user_id UUID,
    
    -- Input/Output
    input_prompt TEXT NOT NULL,
    raw_output TEXT NOT NULL,
    clean_output TEXT NOT NULL,
    
    -- Citation metadata (JSONB for flexibility)
    citations JSONB NOT NULL DEFAULT '[]'::jsonb,
    
    -- Context snapshot
    context_node_ids UUID[] NOT NULL DEFAULT '{}',
    
    -- Metrics
    valid_citation_count INT DEFAULT 0,
    invalid_citation_count INT DEFAULT 0,
    citation_coverage NUMERIC(5, 2) DEFAULT 100.00,
    
    -- Model info
    model_name TEXT,
    model_provider TEXT,
    
    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    -- Optional: link to related entities
    related_entity_type TEXT, -- e.g., 'assignment', 'submission', 'evaluation'
    related_entity_id UUID
);

-- =============================================================================
-- INDEXES
-- =============================================================================

-- Fast lookup by tenant
CREATE INDEX IF NOT EXISTS idx_generation_traces_tenant 
    ON public.generation_traces(tenant_id);

-- Fast lookup by session
CREATE INDEX IF NOT EXISTS idx_generation_traces_session 
    ON public.generation_traces(session_id) 
    WHERE session_id IS NOT NULL;

-- Time-based queries
CREATE INDEX IF NOT EXISTS idx_generation_traces_created 
    ON public.generation_traces(created_at DESC);

-- Find traces that cite a specific node
CREATE INDEX IF NOT EXISTS idx_generation_traces_citations_gin 
    ON public.generation_traces USING GIN(citations);

-- Find traces by node IDs in context
CREATE INDEX IF NOT EXISTS idx_generation_traces_context_gin 
    ON public.generation_traces USING GIN(context_node_ids);

-- Related entity lookup
CREATE INDEX IF NOT EXISTS idx_generation_traces_entity 
    ON public.generation_traces(related_entity_type, related_entity_id) 
    WHERE related_entity_id IS NOT NULL;

-- =============================================================================
-- ROW LEVEL SECURITY (RLS)
-- =============================================================================

ALTER TABLE public.generation_traces ENABLE ROW LEVEL SECURITY;

-- Tenants can only see their own traces
CREATE POLICY "Tenants can view own traces"
    ON public.generation_traces
    FOR SELECT
    USING (tenant_id = (auth.jwt() ->> 'tenant_id')::uuid);

-- Tenants can insert their own traces
CREATE POLICY "Tenants can insert own traces"
    ON public.generation_traces
    FOR INSERT
    WITH CHECK (tenant_id = (auth.jwt() ->> 'tenant_id')::uuid);

-- =============================================================================
-- COMMENTS
-- =============================================================================

COMMENT ON TABLE public.generation_traces IS 
    'Forensic audit log of LLM generations with citation traceability';

COMMENT ON COLUMN public.generation_traces.citations IS 
    'JSONB array of citation annotations with node_id, source_title, status';

COMMENT ON COLUMN public.generation_traces.context_node_ids IS 
    'Array of regulatory node UUIDs that were provided in context';

COMMENT ON COLUMN public.generation_traces.citation_coverage IS 
    'Percentage of valid citations (0-100)';

-- =============================================================================
-- HELPER FUNCTION: Get traces citing a specific node
-- =============================================================================

CREATE OR REPLACE FUNCTION public.get_traces_by_cited_node(
    p_tenant_id UUID,
    p_node_id UUID,
    p_limit INT DEFAULT 100
)
RETURNS TABLE (
    trace_id UUID,
    clean_output TEXT,
    citation_context TEXT,
    created_at TIMESTAMPTZ
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
AS $$
BEGIN
    RETURN QUERY
    SELECT 
        gt.id AS trace_id,
        gt.clean_output,
        (
            SELECT string_agg(c->>'source_excerpt', '; ')
            FROM jsonb_array_elements(gt.citations) AS c
            WHERE c->>'node_id' = p_node_id::text
        ) AS citation_context,
        gt.created_at
    FROM public.generation_traces gt
    WHERE gt.tenant_id = p_tenant_id
      AND gt.citations @> jsonb_build_array(jsonb_build_object('node_id', p_node_id::text))
    ORDER BY gt.created_at DESC
    LIMIT p_limit;
END;
$$;

COMMENT ON FUNCTION public.get_traces_by_cited_node IS
    'Find all generation traces that cited a specific regulatory node';
