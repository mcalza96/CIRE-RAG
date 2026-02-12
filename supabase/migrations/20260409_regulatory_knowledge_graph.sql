-- =============================================================================
-- MIGRATION: REGULATORY KNOWLEDGE GRAPH (Deterministic RAG Foundation)
-- =============================================================================
-- Description: Implements a relational graph structure for institutional 
--              regulations, enabling deterministic conflict resolution in RAG.
--
-- Architecture Philosophy:
-- Unlike traditional "Soft RAG" that treats regulations as flat text, this
-- structure models them as a Knowledge Graph where:
--   - NODES represent individual normative units (Rules, Clauses, Exceptions)
--   - EDGES represent semantic relationships that define precedence
--
-- The key insight is that the 'VETA' (Override) edge type creates an explicit
-- path for the AI to understand which rules take precedence. When retrieving
-- context for the LLM, we first fetch relevant nodes via vector similarity,
-- then traverse the graph to find any overriding exceptions.
--
-- Multi-Tenancy:
--   - Each tenant (institution/teacher) has an isolated graph
--   - RLS policies ensure complete data separation
--   - The tenant_id corresponds to the user's profile.id
--
-- Date: 2026-04-09
-- Author: CISRE Architecture Team
-- =============================================================================

BEGIN;

-- =============================================================================
-- 1. EXTENSIONS
-- =============================================================================
-- Ensure required extensions are available

-- Vector extension for embeddings (should already exist from RAG infrastructure)
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

-- =============================================================================
-- 2. TYPES (Ontology Constraints)
-- =============================================================================
-- Using ENUMs enforces strict ontology at the database level, preventing
-- invalid node/edge types from being inserted.

-- Node Types: The fundamental building blocks of institutional regulations
-- - 'Regla': A general rule or policy statement
-- - 'Cláusula': A specific clause within a rule (more granular)
-- - 'Excepción': An exception that modifies or overrides a rule
-- - 'Concepto': A defined term or concept referenced by rules
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'regulatory_node_type') THEN
        CREATE TYPE public.regulatory_node_type AS ENUM (
            'Regla',
            'Cláusula',
            'Excepción',
            'Concepto'
        );
    END IF;
END $$;

-- Edge Types: Semantic relationships between nodes
-- - 'REQUIERE': Node A requires Node B to be satisfied (prerequisite)
-- - 'VETA': Node A overrides/vetoes Node B (THIS IS KEY FOR DETERMINISM)
-- - 'CONTRADICE': Node A contradicts Node B (conflict marker for human review)
-- - 'AMPLÍA': Node A expands/extends the scope of Node B
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'regulatory_edge_type') THEN
        CREATE TYPE public.regulatory_edge_type AS ENUM (
            'REQUIERE',
            'VETA',
            'CONTRADICE',
            'AMPLÍA'
        );
    END IF;
END $$;

-- =============================================================================
-- 3. TABLE: regulatory_nodes (Graph Vertices)
-- =============================================================================
-- Each node represents a discrete normative unit within the institution's
-- regulatory framework. The combination of structured metadata (node_type,
-- properties) and semantic search (embedding, fts) enables hybrid retrieval.

CREATE TABLE IF NOT EXISTS public.regulatory_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Multi-tenancy: Links to the teacher/institution that owns this node
    -- This is the primary isolation boundary for RLS
    tenant_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    
    -- Ontology: Constrained to valid regulatory entity types
    node_type public.regulatory_node_type NOT NULL,
    
    -- Core Content
    title TEXT NOT NULL,                    -- Canonical name (e.g., "Art. 15 - Asistencia")
    content TEXT NOT NULL,                  -- Full normative text
    
    -- Semantic Search: Vector embedding for hybrid retrieval
    -- Using 1024 dimensions to match Jina v3 standard across CISRE
    embedding vector(1024),
    
    -- Full-Text Search: Auto-generated tsvector for keyword matching
    -- Uses Spanish configuration for proper stemming/stopwords
    fts tsvector GENERATED ALWAYS AS (
        to_tsvector('spanish', title || ' ' || content)
    ) STORED,
    
    -- Flexible Metadata: Stores institution-specific attributes
    -- Examples:
    --   {"articulo": "Art. 15", "capitulo": "III", "fecha_vigencia": "2024-01-01"}
    --   {"autor": "Decanato", "version": 2, "tags": ["asistencia", "evaluación"]}
    properties JSONB DEFAULT '{}'::jsonb,
    
    -- Audit Fields
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Comments for documentation
COMMENT ON TABLE public.regulatory_nodes IS 
    'Stores vertices of the Regulatory Knowledge Graph. Each node represents a discrete normative unit (Rule, Clause, Exception, or Concept).';
COMMENT ON COLUMN public.regulatory_nodes.tenant_id IS 
    'Foreign key to profiles.id, defining ownership for multi-tenant isolation.';
COMMENT ON COLUMN public.regulatory_nodes.embedding IS 
    'Vector embedding (Jina v3, 1024d) for semantic similarity search in hybrid retrieval.';
COMMENT ON COLUMN public.regulatory_nodes.properties IS 
    'Flexible JSONB for institution-specific metadata (article numbers, effective dates, authors, etc.).';

-- =============================================================================
-- 4. TABLE: regulatory_edges (Graph Edges)
-- =============================================================================
-- Edges define the semantic relationships between nodes. The edge_type is
-- crucial for deterministic reasoning:
--
-- When the RAG system retrieves a "Regla" node via vector search, it MUST
-- also query for any connected 'VETA' edges to find overriding exceptions.
-- This is the "determinism" mechanism: exceptions explicitly veto rules.

CREATE TABLE IF NOT EXISTS public.regulatory_edges (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Relationship endpoints
    source_id UUID NOT NULL REFERENCES public.regulatory_nodes(id) ON DELETE CASCADE,
    target_id UUID NOT NULL REFERENCES public.regulatory_nodes(id) ON DELETE CASCADE,
    
    -- Semantic relationship type (constrained by ENUM)
    edge_type public.regulatory_edge_type NOT NULL,
    
    -- Weight: Strength of the relationship (0.0 to 1.0)
    -- Use cases:
    --   - VETA with weight=1.0: Complete override
    --   - VETA with weight=0.5: Partial override (may need context)
    --   - AMPLÍA with weight: Degree of extension
    weight FLOAT DEFAULT 1.0 CHECK (weight >= 0.0 AND weight <= 1.0),
    
    -- Flexible Metadata: Stores edge-specific context
    -- Examples:
    --   {"razon": "Excepción para casos médicos documentados"}
    --   {"condicion": "Solo aplica en periodo de exámenes finales"}
    metadata JSONB DEFAULT '{}'::jsonb,
    
    -- Audit Fields
    created_at TIMESTAMPTZ DEFAULT now(),
    
    -- Prevent duplicate edges between same nodes with same type
    CONSTRAINT uq_regulatory_edge UNIQUE (source_id, target_id, edge_type),
    
    -- Prevent self-loops
    CONSTRAINT chk_no_self_loop CHECK (source_id != target_id)
);

-- Comments for documentation
COMMENT ON TABLE public.regulatory_edges IS 
    'Stores edges of the Regulatory Knowledge Graph. Each edge represents a semantic relationship between two normative nodes.';
COMMENT ON COLUMN public.regulatory_edges.edge_type IS 
    'Semantic relationship: REQUIERE (prerequisite), VETA (override/exception), CONTRADICE (conflict), AMPLÍA (extends).';
COMMENT ON COLUMN public.regulatory_edges.weight IS 
    'Relationship strength (0.0-1.0). For VETA edges, 1.0 = full override, <1.0 = partial/conditional.';

-- =============================================================================
-- 5. INDEXES
-- =============================================================================
-- Strategic indexing for common query patterns in graph traversal and
-- hybrid (vector + keyword) search.

-- 5.1 Node Indexes

-- Fast lookup by tenant (RLS primary filter)
CREATE INDEX IF NOT EXISTS idx_regulatory_nodes_tenant_id 
ON public.regulatory_nodes(tenant_id);

-- Vector similarity search (HNSW for fast ANN)
CREATE INDEX IF NOT EXISTS idx_regulatory_nodes_embedding 
ON public.regulatory_nodes USING hnsw (embedding vector_cosine_ops);

-- Full-text search (GIN for efficient tsvector queries)
CREATE INDEX IF NOT EXISTS idx_regulatory_nodes_fts 
ON public.regulatory_nodes USING gin (fts);

-- Filter by node type (useful for scoped queries)
CREATE INDEX IF NOT EXISTS idx_regulatory_nodes_type 
ON public.regulatory_nodes(node_type);

-- 5.2 Edge Indexes

-- Graph traversal: Find all outgoing edges from a node
CREATE INDEX IF NOT EXISTS idx_regulatory_edges_source 
ON public.regulatory_edges(source_id);

-- Graph traversal: Find all incoming edges to a node
CREATE INDEX IF NOT EXISTS idx_regulatory_edges_target 
ON public.regulatory_edges(target_id);

-- Find edges by type (e.g., all VETA relationships)
CREATE INDEX IF NOT EXISTS idx_regulatory_edges_type 
ON public.regulatory_edges(edge_type);

-- Composite index for common query: "Find all VETA edges from this node"
CREATE INDEX IF NOT EXISTS idx_regulatory_edges_source_type 
ON public.regulatory_edges(source_id, edge_type);

-- =============================================================================
-- 6. ROW LEVEL SECURITY (Multi-Tenant Isolation)
-- =============================================================================
-- RLS ensures complete data isolation between tenants. Each query is
-- automatically filtered to only return rows belonging to the authenticated user.

-- 6.1 Enable RLS
ALTER TABLE public.regulatory_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.regulatory_edges ENABLE ROW LEVEL SECURITY;

-- 6.2 Policies for regulatory_nodes
-- Tenants can only access nodes they own (or if they are admin)

DROP POLICY IF EXISTS "Tenants can manage their own regulatory nodes" ON public.regulatory_nodes;
CREATE POLICY "Tenants can manage their own regulatory nodes"
ON public.regulatory_nodes
FOR ALL
TO authenticated
USING (
    tenant_id = auth.uid() OR public.is_admin()
)
WITH CHECK (
    tenant_id = auth.uid() OR public.is_admin()
);

-- 6.3 Policies for regulatory_edges
-- Access control delegates to the source node's tenant ownership
-- This ensures edges between nodes of different tenants are impossible

DROP POLICY IF EXISTS "Tenants can manage edges of their own nodes" ON public.regulatory_edges;
CREATE POLICY "Tenants can manage edges of their own nodes"
ON public.regulatory_edges
FOR ALL
TO authenticated
USING (
    EXISTS (
        SELECT 1 FROM public.regulatory_nodes n
        WHERE n.id = public.regulatory_edges.source_id
        AND (n.tenant_id = auth.uid() OR public.is_admin())
    )
)
WITH CHECK (
    EXISTS (
        SELECT 1 FROM public.regulatory_nodes n
        WHERE n.id = public.regulatory_edges.source_id
        AND (n.tenant_id = auth.uid() OR public.is_admin())
    )
);

-- =============================================================================
-- 7. FUNCTION: get_regulatory_path (Graph Traversal RPC)
-- =============================================================================
-- This function is the core of the "deterministic" RAG behavior.
--
-- Given a starting node (e.g., a Rule retrieved via vector search), it finds
-- all connected nodes that MODIFY the original node. Specifically:
--   - 'VETA' edges: Exceptions that override the rule
--   - 'AMPLÍA' edges: Extensions that expand the rule's scope
--
-- The result can be used to:
--   1. Include exception context in the LLM prompt
--   2. Filter out rules that have been overridden
--   3. Build a complete "regulatory context" for accurate responses
--
-- Parameters:
--   - source_node_id: The starting node (usually a Rule from vector search)
--   - user_tenant_id: The tenant ID for RLS filtering (passed explicitly for security)
--   - max_depth: Maximum traversal depth (default 5, prevents infinite loops)
--   - edge_types: Array of edge types to follow (default: VETA, AMPLÍA)
--
-- Returns:
--   Table of nodes in the modification path, with their relationship type and depth

CREATE OR REPLACE FUNCTION public.get_regulatory_path(
    source_node_id UUID,
    user_tenant_id UUID,
    max_depth INT DEFAULT 5,
    edge_types public.regulatory_edge_type[] DEFAULT ARRAY['VETA', 'AMPLÍA']::public.regulatory_edge_type[]
)
RETURNS TABLE (
    node_id UUID,
    node_type public.regulatory_node_type,
    title TEXT,
    content TEXT,
    properties JSONB,
    edge_type public.regulatory_edge_type,
    depth INT,
    path_weight FLOAT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    -- Validate that the source node belongs to the tenant
    IF NOT EXISTS (
        SELECT 1 FROM public.regulatory_nodes 
        WHERE id = source_node_id AND tenant_id = user_tenant_id
    ) THEN
        RAISE EXCEPTION 'Source node not found or access denied';
    END IF;

    RETURN QUERY
    WITH RECURSIVE path_traversal AS (
        -- Base case: Direct connections from the source node
        SELECT 
            e.target_id AS node_id,
            e.edge_type,
            e.weight AS accumulated_weight,
            1 AS current_depth,
            ARRAY[source_node_id, e.target_id] AS visited_nodes
        FROM public.regulatory_edges e
        JOIN public.regulatory_nodes n ON n.id = e.source_id
        WHERE e.source_id = source_node_id
          AND e.edge_type = ANY(edge_types)
          AND n.tenant_id = user_tenant_id

        UNION ALL

        -- Recursive case: Follow edges from previously found nodes
        SELECT 
            e.target_id AS node_id,
            e.edge_type,
            pt.accumulated_weight * e.weight AS accumulated_weight,
            pt.current_depth + 1 AS current_depth,
            pt.visited_nodes || e.target_id
        FROM public.regulatory_edges e
        JOIN path_traversal pt ON e.source_id = pt.node_id
        JOIN public.regulatory_nodes n ON n.id = e.source_id
        WHERE pt.current_depth < max_depth
          AND e.edge_type = ANY(edge_types)
          AND n.tenant_id = user_tenant_id
          -- Prevent cycles by checking if we've already visited this node
          AND NOT (e.target_id = ANY(pt.visited_nodes))
    )
    -- Final selection: Join with node data
    SELECT 
        n.id AS node_id,
        n.node_type,
        n.title,
        n.content,
        n.properties,
        pt.edge_type,
        pt.current_depth AS depth,
        pt.accumulated_weight AS path_weight
    FROM path_traversal pt
    JOIN public.regulatory_nodes n ON n.id = pt.node_id
    WHERE n.tenant_id = user_tenant_id
    ORDER BY pt.current_depth, pt.accumulated_weight DESC;
END;
$$;

-- Grant execute permission
GRANT EXECUTE ON FUNCTION public.get_regulatory_path(UUID, UUID, INT, public.regulatory_edge_type[]) 
TO authenticated;
GRANT EXECUTE ON FUNCTION public.get_regulatory_path(UUID, UUID, INT, public.regulatory_edge_type[]) 
TO service_role;

-- Documentation
COMMENT ON FUNCTION public.get_regulatory_path IS 
    'Traverses the regulatory graph from a source node, following specified edge types (default: VETA, AMPLÍA). Returns all nodes that modify the source rule, enabling deterministic conflict resolution in RAG.';

-- =============================================================================
-- 8. FUNCTION: hybrid_regulatory_search (Semantic + Graph Fusion)
-- =============================================================================
-- Combines vector similarity search with graph traversal for complete context.
--
-- Workflow:
--   1. Find top-K nodes matching the query via embedding similarity
--   2. For each match, traverse the graph to find overriding exceptions
--   3. Return nodes with their modifiers, sorted by relevance
--
-- This enables the RAG system to always include relevant exceptions in context.

CREATE OR REPLACE FUNCTION public.hybrid_regulatory_search(
    query_text TEXT,
    query_embedding vector(1024),
    user_tenant_id UUID,
    match_threshold FLOAT DEFAULT 0.7,
    match_count INT DEFAULT 10
)
RETURNS TABLE (
    node_id UUID,
    node_type public.regulatory_node_type,
    title TEXT,
    content TEXT,
    properties JSONB,
    similarity FLOAT,
    has_overrides BOOLEAN,
    override_node_ids UUID[]
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RETURN QUERY
    WITH 
    -- Step 1: Vector search for relevant nodes
    vector_matches AS (
        SELECT 
            n.id,
            n.node_type,
            n.title,
            n.content,
            n.properties,
            (1 - (n.embedding <=> query_embedding)) AS similarity
        FROM public.regulatory_nodes n
        WHERE n.tenant_id = user_tenant_id
          AND n.embedding IS NOT NULL
          AND (1 - (n.embedding <=> query_embedding)) > match_threshold
        ORDER BY n.embedding <=> query_embedding
        LIMIT match_count
    ),
    -- Step 2: Find VETA edges for each matched node
    overrides AS (
        SELECT 
            vm.id AS source_id,
            array_agg(e.target_id) AS override_ids
        FROM vector_matches vm
        LEFT JOIN public.regulatory_edges e ON e.source_id = vm.id
        WHERE e.edge_type = 'VETA'
        GROUP BY vm.id
    )
    -- Step 3: Combine results
    SELECT 
        vm.id AS node_id,
        vm.node_type,
        vm.title,
        vm.content,
        vm.properties,
        vm.similarity,
        COALESCE(array_length(o.override_ids, 1) > 0, FALSE) AS has_overrides,
        COALESCE(o.override_ids, ARRAY[]::UUID[]) AS override_node_ids
    FROM vector_matches vm
    LEFT JOIN overrides o ON o.source_id = vm.id
    ORDER BY vm.similarity DESC;
END;
$$;

-- Grant execute permission
GRANT EXECUTE ON FUNCTION public.hybrid_regulatory_search(TEXT, vector(1024), UUID, FLOAT, INT) 
TO authenticated;
GRANT EXECUTE ON FUNCTION public.hybrid_regulatory_search(TEXT, vector(1024), UUID, FLOAT, INT) 
TO service_role;

-- Documentation
COMMENT ON FUNCTION public.hybrid_regulatory_search IS 
    'Hybrid search combining vector similarity with graph-aware override detection. Returns matching regulatory nodes along with any VETA (override) relationships.';

-- =============================================================================
-- 9. UPDATE TRIGGER (Audit Trail)
-- =============================================================================
-- Automatically update the updated_at timestamp on node modifications

CREATE OR REPLACE FUNCTION public.update_regulatory_node_timestamp()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_regulatory_node_updated ON public.regulatory_nodes;
CREATE TRIGGER trg_regulatory_node_updated
BEFORE UPDATE ON public.regulatory_nodes
FOR EACH ROW
EXECUTE FUNCTION public.update_regulatory_node_timestamp();

-- =============================================================================
-- 10. SEED DATA (Example: Attendance Rule with Medical Exception)
-- =============================================================================
-- This demonstrates the deterministic override pattern.
--
-- Scenario:
--   - "Regla de Asistencia Obligatoria" requires 80% attendance
--   - "Excepción Médica" VETA (overrides) this requirement for medical cases
--
-- When the AI queries about attendance requirements for a student with medical
-- documentation, the graph traversal will return both nodes. The RAG system
-- can then prioritize the exception in the LLM prompt.

-- Note: We use a DO block with a variable to capture the generated UUIDs
-- In production, you would replace 'YOUR_TENANT_UUID' with an actual tenant ID

DO $$
DECLARE
    seed_tenant_id UUID;
    regla_asistencia_id UUID;
    excepcion_medica_id UUID;
    concepto_asistencia_id UUID;
BEGIN
    -- Check if we should create seed data (only if no nodes exist)
    -- This makes the migration idempotent
    IF EXISTS (SELECT 1 FROM public.regulatory_nodes LIMIT 1) THEN
        RAISE NOTICE 'Seed data skipped: regulatory_nodes table is not empty';
        RETURN;
    END IF;

    -- Get a sample tenant for seeding (first admin or teacher)
    SELECT id INTO seed_tenant_id 
    FROM public.profiles 
    WHERE role IN ('admin', 'teacher') 
    LIMIT 1;

    IF seed_tenant_id IS NULL THEN
        RAISE NOTICE 'Seed data skipped: no admin or teacher profile found';
        RETURN;
    END IF;

    -- Create Concept Node: Asistencia
    INSERT INTO public.regulatory_nodes (tenant_id, node_type, title, content, properties)
    VALUES (
        seed_tenant_id,
        'Concepto',
        'Asistencia',
        'La asistencia se define como la presencia física o virtual del estudiante durante el horario programado de clase, verificada por el sistema de registro institucional.',
        '{"definido_en": "Glosario Reglamento Académico", "version": 1}'::jsonb
    )
    RETURNING id INTO concepto_asistencia_id;

    -- Create Rule Node: Asistencia Obligatoria
    INSERT INTO public.regulatory_nodes (tenant_id, node_type, title, content, properties)
    VALUES (
        seed_tenant_id,
        'Regla',
        'Art. 15 - Asistencia Obligatoria',
        'Los estudiantes matriculados en cursos regulares deberán cumplir con un mínimo del 80% de asistencia a las sesiones programadas. El incumplimiento de este requisito resultará en la pérdida del derecho a evaluación ordinaria, debiendo optar por evaluación extraordinaria.',
        '{"articulo": "Art. 15", "capitulo": "III - Derechos y Deberes", "fecha_vigencia": "2024-01-01", "sancion": "Pérdida evaluación ordinaria"}'::jsonb
    )
    RETURNING id INTO regla_asistencia_id;

    -- Create Exception Node: Excepción Médica
    INSERT INTO public.regulatory_nodes (tenant_id, node_type, title, content, properties)
    VALUES (
        seed_tenant_id,
        'Excepción',
        'Art. 15.1 - Excepción por Causa Médica',
        'Se exime del requisito mínimo de asistencia establecido en el Art. 15 a aquellos estudiantes que presenten documentación médica oficial expedida por el Servicio de Salud Universitario o institución de salud reconocida. La documentación debe ser presentada dentro de los 5 días hábiles posteriores a la ausencia y especificar el periodo de incapacidad.',
        '{"articulo": "Art. 15.1", "capitulo": "III - Derechos y Deberes", "fecha_vigencia": "2024-01-01", "requisitos": ["Documentación médica oficial", "Presentación en 5 días hábiles", "Especificación de periodo"]}'::jsonb
    )
    RETURNING id INTO excepcion_medica_id;

    -- Create Edge: Excepción VETA Regla
    INSERT INTO public.regulatory_edges (source_id, target_id, edge_type, weight, metadata)
    VALUES (
        regla_asistencia_id,
        excepcion_medica_id,
        'VETA',
        1.0,
        '{"razon": "Excepción establecida para casos de salud documentados", "autor": "Consejo Académico"}'::jsonb
    );

    -- Create Edge: Regla REQUIERE Concepto
    INSERT INTO public.regulatory_edges (source_id, target_id, edge_type, weight, metadata)
    VALUES (
        regla_asistencia_id,
        concepto_asistencia_id,
        'REQUIERE',
        1.0,
        '{"tipo": "Definición referenciada"}'::jsonb
    );

    RAISE NOTICE 'Seed data created successfully for tenant %', seed_tenant_id;
    RAISE NOTICE 'Rule: %, Exception: %, Concept: %', regla_asistencia_id, excepcion_medica_id, concepto_asistencia_id;
END $$;

COMMIT;

-- =============================================================================
-- USAGE EXAMPLES (For Reference - Not Executed)
-- =============================================================================
/*
-- Example 1: Find all nodes that override a rule
SELECT * FROM public.get_regulatory_path(
    'rule-uuid-here'::UUID,
    'tenant-uuid-here'::UUID,
    5,
    ARRAY['VETA']::regulatory_edge_type[]
);

-- Example 2: Hybrid search with override detection
SELECT * FROM public.hybrid_regulatory_search(
    'requisitos de asistencia',                           -- Query text
    '[0.1, 0.2, ...]'::vector(1024),                     -- Query embedding
    'tenant-uuid-here'::UUID,                             -- Tenant ID
    0.7,                                                  -- Similarity threshold
    10                                                    -- Max results
);

-- Example 3: Direct graph query - find all exceptions
SELECT 
    r.title AS rule_title,
    e.edge_type,
    ex.title AS exception_title
FROM public.regulatory_nodes r
JOIN public.regulatory_edges e ON e.source_id = r.id
JOIN public.regulatory_nodes ex ON ex.id = e.target_id
WHERE r.node_type = 'Regla'
  AND e.edge_type = 'VETA'
  AND r.tenant_id = auth.uid();

-- Example 4: Count nodes by type per tenant
SELECT 
    node_type,
    COUNT(*) as count
FROM public.regulatory_nodes
WHERE tenant_id = auth.uid()
GROUP BY node_type;
*/
