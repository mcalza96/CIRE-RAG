-- =============================================================================
-- MIGRATION: RAPTOR Hierarchy Columns for regulatory_nodes
-- =============================================================================
-- Description: Adds columns to support RAPTOR-style recursive summarization.
--   - level: Indicates the position in the summary hierarchy (0 = base chunk).
--   - children_ids: Array of child node UUIDs for tree traversal.
--   - source_document_id: Optional link to the original source document.
--
-- Date: 2026-02-05
-- Author: CISRE Architecture Team
-- =============================================================================

BEGIN;

-- =============================================================================
-- 1. ADD HIERARCHY COLUMNS
-- =============================================================================

-- Level in the RAPTOR tree:
-- 0 = Base chunk (from JinaLateChunker)
-- 1 = First-level summary (cluster of base chunks)
-- 2 = Second-level summary (cluster of L1 summaries)
-- N = Higher abstractions
ALTER TABLE public.regulatory_nodes
ADD COLUMN IF NOT EXISTS level INT DEFAULT 0;

-- Child node IDs for tree traversal.
-- A summary node at level N has children at level N-1.
-- Base chunks (level 0) have an empty array.
ALTER TABLE public.regulatory_nodes
ADD COLUMN IF NOT EXISTS children_ids UUID[] DEFAULT ARRAY[]::UUID[];

-- Optional: Link to original source document for lineage tracking.
-- This helps trace summaries back to their source material.
ALTER TABLE public.regulatory_nodes
ADD COLUMN IF NOT EXISTS source_document_id UUID REFERENCES public.source_documents(id) ON DELETE SET NULL;

-- =============================================================================
-- 2. CONSTRAINTS
-- =============================================================================

-- Ensure level is non-negative
ALTER TABLE public.regulatory_nodes
ADD CONSTRAINT chk_level_non_negative CHECK (level >= 0);

-- =============================================================================
-- 3. INDEXES FOR RAPTOR QUERIES
-- =============================================================================

-- Fast lookup by level (e.g., "get all L1 summaries for a tenant")
CREATE INDEX IF NOT EXISTS idx_regulatory_nodes_level
ON public.regulatory_nodes(level);

-- Composite index for hierarchical queries within a tenant
CREATE INDEX IF NOT EXISTS idx_regulatory_nodes_tenant_level
ON public.regulatory_nodes(tenant_id, level);

-- GIN index for array containment queries (e.g., "find parents of this node")
CREATE INDEX IF NOT EXISTS idx_regulatory_nodes_children_ids
ON public.regulatory_nodes USING GIN (children_ids);

-- =============================================================================
-- 4. DOCUMENTATION
-- =============================================================================

COMMENT ON COLUMN public.regulatory_nodes.level IS
    'RAPTOR hierarchy level. 0 = base chunk, 1+ = summary nodes.';

COMMENT ON COLUMN public.regulatory_nodes.children_ids IS
    'Array of child node UUIDs. For summary nodes, these are the nodes that were clustered and summarized.';

COMMENT ON COLUMN public.regulatory_nodes.source_document_id IS
    'Optional reference to the original source document for lineage tracking.';

COMMIT;
