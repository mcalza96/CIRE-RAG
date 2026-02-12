-- Migration: Graph-Chunk Strong Grounding
-- Description:
-- 1) Extends graph ontology enums for multimodal table modeling.
-- 2) Adds graph_node_chunks junction table to persist node<->chunk lineage.

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. Extend node/edge enums (additive)
-- -----------------------------------------------------------------------------

ALTER TYPE public.regulatory_node_type ADD VALUE IF NOT EXISTS 'TABLE';
ALTER TYPE public.regulatory_node_type ADD VALUE IF NOT EXISTS 'ROW';
ALTER TYPE public.regulatory_node_type ADD VALUE IF NOT EXISTS 'COLUMN';
ALTER TYPE public.regulatory_node_type ADD VALUE IF NOT EXISTS 'CELL';

ALTER TYPE public.regulatory_edge_type ADD VALUE IF NOT EXISTS 'PERTENECE_A';

-- -----------------------------------------------------------------------------
-- 2. Junction table: graph_node_chunks (strong grounding FK)
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.graph_node_chunks (
    node_id UUID NOT NULL REFERENCES public.regulatory_nodes(id) ON DELETE CASCADE,
    chunk_id UUID NOT NULL REFERENCES public.content_chunks(id) ON DELETE CASCADE,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (node_id, chunk_id),
    CONSTRAINT chk_graph_node_chunks_confidence_range CHECK (confidence >= 0.0 AND confidence <= 1.0)
);

CREATE INDEX IF NOT EXISTS idx_graph_node_chunks_chunk_id
    ON public.graph_node_chunks (chunk_id);

CREATE INDEX IF NOT EXISTS idx_graph_node_chunks_node_id
    ON public.graph_node_chunks (node_id);

COMMENT ON TABLE public.graph_node_chunks IS
    'Strong grounding lineage between knowledge graph nodes and source content chunks.';

COMMENT ON COLUMN public.graph_node_chunks.confidence IS
    'Extraction confidence for the node<-chunk grounding relation (0..1).';

COMMIT;
