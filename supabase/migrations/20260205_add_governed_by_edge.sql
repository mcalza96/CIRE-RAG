-- =============================================================================
-- MIGRATION: Add GOVERNED_BY Edge Type for Cross-Modal Bridging
-- =============================================================================
-- Description: Extends the regulatory_edge_type ENUM to include GOVERNED_BY,
--              enabling automatic linking between RAPTOR academic clusters
--              and institutional regulatory nodes.
--
-- Purpose:
--   When academic content (RAPTOR summaries) is semantically similar to
--   regulatory nodes, the system creates GOVERNED_BY edges. This allows
--   the RAG retrieval to "drag" relevant regulations automatically.
--
-- Date: 2026-02-05
-- Author: SemanticBridgeService Implementation
-- =============================================================================

BEGIN;

-- Add new edge type to the existing ENUM
-- PostgreSQL allows adding values to ENUMs without recreating the type
ALTER TYPE public.regulatory_edge_type ADD VALUE IF NOT EXISTS 'GOVERNED_BY';

-- Documentation
COMMENT ON TYPE public.regulatory_edge_type IS 
    'Semantic relationship types: REQUIERE (prerequisite), VETA (override), CONTRADICE (conflict), AMPLÍA (extends), GOVERNED_BY (academic-to-regulatory bridge)';

COMMIT;
