BEGIN;

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;

-- =============================================================================
-- Phase 1: Knowledge Layer Schema (GraphRAG foundation)
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.knowledge_entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES public.institutions(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    type TEXT,
    description TEXT,
    embedding vector(1536),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT knowledge_entities_id_tenant_unique UNIQUE (id, tenant_id)
);

CREATE TABLE IF NOT EXISTS public.knowledge_relations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES public.institutions(id) ON DELETE CASCADE,
    source_entity_id UUID NOT NULL,
    target_entity_id UUID NOT NULL,
    relation_type TEXT NOT NULL,
    description TEXT,
    weight DOUBLE PRECISION NOT NULL DEFAULT 1.0 CHECK (weight >= 0.0),
    embedding vector(1536),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT knowledge_relations_source_fk
        FOREIGN KEY (source_entity_id, tenant_id)
        REFERENCES public.knowledge_entities(id, tenant_id)
        ON DELETE CASCADE,
    CONSTRAINT knowledge_relations_target_fk
        FOREIGN KEY (target_entity_id, tenant_id)
        REFERENCES public.knowledge_entities(id, tenant_id)
        ON DELETE CASCADE,
    CONSTRAINT knowledge_relations_no_self_loop CHECK (source_entity_id <> target_entity_id),
    CONSTRAINT knowledge_relations_unique_edge UNIQUE (tenant_id, source_entity_id, target_entity_id, relation_type)
);

CREATE TABLE IF NOT EXISTS public.knowledge_communities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES public.institutions(id) ON DELETE CASCADE,
    community_id INTEGER NOT NULL,
    level INTEGER NOT NULL CHECK (level >= 0),
    summary TEXT NOT NULL,
    embedding vector(1536),
    members JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT knowledge_communities_unique_per_level UNIQUE (tenant_id, community_id, level)
);

CREATE TABLE IF NOT EXISTS public.knowledge_node_provenance (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES public.institutions(id) ON DELETE CASCADE,
    entity_id UUID NOT NULL,
    chunk_id UUID NOT NULL REFERENCES public.content_chunks(id) ON DELETE CASCADE,
    text_snippet TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT knowledge_node_provenance_entity_fk
        FOREIGN KEY (entity_id, tenant_id)
        REFERENCES public.knowledge_entities(id, tenant_id)
        ON DELETE CASCADE,
    CONSTRAINT knowledge_node_provenance_unique UNIQUE (tenant_id, entity_id, chunk_id)
);

-- =============================================================================
-- Comments
-- =============================================================================

COMMENT ON COLUMN public.knowledge_entities.description IS
    'AI-generated canonical summary for the entity; used for semantic retrieval and grounding.';

COMMENT ON COLUMN public.knowledge_relations.description IS
    'Evidence/explanation for why the relationship exists between source and target entities.';

COMMENT ON COLUMN public.knowledge_communities.summary IS
    'Global-search summary of a community cluster generated from graph structure.';

COMMENT ON COLUMN public.knowledge_communities.members IS
    'JSON array of entity UUIDs that belong to this community snapshot.';

COMMENT ON COLUMN public.knowledge_node_provenance.text_snippet IS
    'Exact source span used to extract the entity, enabling citations and UI highlighting.';

-- =============================================================================
-- Indexes
-- =============================================================================

-- Tenant filters (RLS + scoped queries)
CREATE INDEX IF NOT EXISTS idx_knowledge_entities_tenant_id
    ON public.knowledge_entities(tenant_id);

CREATE INDEX IF NOT EXISTS idx_knowledge_relations_tenant_id
    ON public.knowledge_relations(tenant_id);

CREATE INDEX IF NOT EXISTS idx_knowledge_communities_tenant_id
    ON public.knowledge_communities(tenant_id);

CREATE INDEX IF NOT EXISTS idx_knowledge_node_provenance_tenant_id
    ON public.knowledge_node_provenance(tenant_id);

-- Required standard indexes
CREATE INDEX IF NOT EXISTS idx_knowledge_entities_name
    ON public.knowledge_entities(name);

CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_entities_tenant_name_ci
    ON public.knowledge_entities(tenant_id, lower(name));

CREATE INDEX IF NOT EXISTS idx_knowledge_relations_source_entity_id
    ON public.knowledge_relations(source_entity_id);

CREATE INDEX IF NOT EXISTS idx_knowledge_relations_target_entity_id
    ON public.knowledge_relations(target_entity_id);

CREATE INDEX IF NOT EXISTS idx_knowledge_communities_level
    ON public.knowledge_communities(level);

CREATE INDEX IF NOT EXISTS idx_knowledge_node_provenance_entity_id
    ON public.knowledge_node_provenance(entity_id);

CREATE INDEX IF NOT EXISTS idx_knowledge_node_provenance_chunk_id
    ON public.knowledge_node_provenance(chunk_id);

-- Required HNSW vector indexes
CREATE INDEX IF NOT EXISTS idx_knowledge_entities_embedding_hnsw
    ON public.knowledge_entities USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_knowledge_relations_embedding_hnsw
    ON public.knowledge_relations USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_knowledge_communities_embedding_hnsw
    ON public.knowledge_communities USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;

-- =============================================================================
-- Timestamp maintenance
-- =============================================================================

CREATE OR REPLACE FUNCTION public.set_knowledge_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_knowledge_entities_updated_at ON public.knowledge_entities;
CREATE TRIGGER trg_knowledge_entities_updated_at
BEFORE UPDATE ON public.knowledge_entities
FOR EACH ROW
EXECUTE FUNCTION public.set_knowledge_updated_at();

DROP TRIGGER IF EXISTS trg_knowledge_relations_updated_at ON public.knowledge_relations;
CREATE TRIGGER trg_knowledge_relations_updated_at
BEFORE UPDATE ON public.knowledge_relations
FOR EACH ROW
EXECUTE FUNCTION public.set_knowledge_updated_at();

DROP TRIGGER IF EXISTS trg_knowledge_communities_updated_at ON public.knowledge_communities;
CREATE TRIGGER trg_knowledge_communities_updated_at
BEFORE UPDATE ON public.knowledge_communities
FOR EACH ROW
EXECUTE FUNCTION public.set_knowledge_updated_at();

-- =============================================================================
-- Row Level Security
-- =============================================================================

ALTER TABLE public.knowledge_entities ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.knowledge_entities FORCE ROW LEVEL SECURITY;

ALTER TABLE public.knowledge_relations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.knowledge_relations FORCE ROW LEVEL SECURITY;

ALTER TABLE public.knowledge_communities ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.knowledge_communities FORCE ROW LEVEL SECURITY;

ALTER TABLE public.knowledge_node_provenance ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.knowledge_node_provenance FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Knowledge entities tenant isolation" ON public.knowledge_entities;
CREATE POLICY "Knowledge entities tenant isolation"
ON public.knowledge_entities
FOR ALL
TO authenticated
USING (
    public.is_admin() OR EXISTS (
        SELECT 1
        FROM public.memberships m
        WHERE m.institution_id = public.knowledge_entities.tenant_id
          AND m.user_id = auth.uid()
    )
)
WITH CHECK (
    public.is_admin() OR EXISTS (
        SELECT 1
        FROM public.memberships m
        WHERE m.institution_id = public.knowledge_entities.tenant_id
          AND m.user_id = auth.uid()
    )
);

DROP POLICY IF EXISTS "Knowledge relations tenant isolation" ON public.knowledge_relations;
CREATE POLICY "Knowledge relations tenant isolation"
ON public.knowledge_relations
FOR ALL
TO authenticated
USING (
    public.is_admin() OR EXISTS (
        SELECT 1
        FROM public.memberships m
        WHERE m.institution_id = public.knowledge_relations.tenant_id
          AND m.user_id = auth.uid()
    )
)
WITH CHECK (
    public.is_admin() OR EXISTS (
        SELECT 1
        FROM public.memberships m
        WHERE m.institution_id = public.knowledge_relations.tenant_id
          AND m.user_id = auth.uid()
    )
);

DROP POLICY IF EXISTS "Knowledge communities tenant isolation" ON public.knowledge_communities;
CREATE POLICY "Knowledge communities tenant isolation"
ON public.knowledge_communities
FOR ALL
TO authenticated
USING (
    public.is_admin() OR EXISTS (
        SELECT 1
        FROM public.memberships m
        WHERE m.institution_id = public.knowledge_communities.tenant_id
          AND m.user_id = auth.uid()
    )
)
WITH CHECK (
    public.is_admin() OR EXISTS (
        SELECT 1
        FROM public.memberships m
        WHERE m.institution_id = public.knowledge_communities.tenant_id
          AND m.user_id = auth.uid()
    )
);

DROP POLICY IF EXISTS "Knowledge provenance tenant isolation" ON public.knowledge_node_provenance;
CREATE POLICY "Knowledge provenance tenant isolation"
ON public.knowledge_node_provenance
FOR ALL
TO authenticated
USING (
    public.is_admin() OR EXISTS (
        SELECT 1
        FROM public.memberships m
        WHERE m.institution_id = public.knowledge_node_provenance.tenant_id
          AND m.user_id = auth.uid()
    )
)
WITH CHECK (
    public.is_admin() OR EXISTS (
        SELECT 1
        FROM public.memberships m
        WHERE m.institution_id = public.knowledge_node_provenance.tenant_id
          AND m.user_id = auth.uid()
    )
);

COMMIT;
