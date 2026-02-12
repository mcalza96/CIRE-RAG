-- =============================================================================
-- MIGRATION: HIERARCHICAL RAG TOPOLOGY
-- Description: Establishes the data foundations for a navigable Knowledge Graph.
--              Replaces the flat "Bag of Chunks" model with a Tree structure
--              using PostgreSQL `ltree` extension for scoped retrieval.
-- Date: 2026-03-13
-- =============================================================================

BEGIN;

-- 1. EXTENSIONS
-- =============================================================================
-- Enable ltree for efficient hierarchical data storage and querying.
CREATE EXTENSION IF NOT EXISTS ltree WITH SCHEMA public;
-- Ensure vector extension is active for embeddings.
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


-- 2. TABLE: course_nodes (The Skeleton)
-- =============================================================================
-- This table represents the logical structure of the curriculum (Course -> Unit -> Lesson -> Topic).
-- It acts as the "backbone" of the knowledge graph.

CREATE TABLE IF NOT EXISTS public.course_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id UUID NOT NULL REFERENCES public.courses(id) ON DELETE CASCADE,
    
    -- The Magic Column: 'root.unit1.lesson3.topic5'
    -- usage: WHERE node_path <@ 'root.unit1' (Selects entire subtree)
    node_path ltree NOT NULL,
    
    -- Level types for semantic understanding of the hierarchy depth
    level_type TEXT NOT NULL CHECK (level_type IN ('ROOT', 'UNIT', 'MODULE', 'LESSON', 'TOPIC', 'ATOM')),
    
    title TEXT NOT NULL,
    
    -- AI Generated summary of THIS underlying node (not recursive)
    -- Essential for the "Architect Agent" to understand coverage without reading chunks.
    summary TEXT,
    
    -- Flexible metadata: page_range, duration, prerequisites, etc.
    metadata JSONB DEFAULT '{}'::jsonb,
    
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),

    -- Constraint: Ensure path uniqueness within a course to avoid structural collisions
    CONSTRAINT uq_course_node_path UNIQUE (course_id, node_path)
);

-- Indexes for course_nodes
-- GIST index is CRITICAL for ltree performance (operators: <@, @>, ~)
CREATE INDEX IF NOT EXISTS idx_course_nodes_path_gist ON public.course_nodes USING GIST(node_path);
-- BTree index for standard filtering
CREATE INDEX IF NOT EXISTS idx_course_nodes_course_id ON public.course_nodes(course_id);


-- 3. TABLE: knowledge_chunks (The Flesh)
-- =============================================================================
-- Stores the actual content fragments with vector embeddings.
-- Intentionally des-normalized (includes chunk_path) to allow fast Scoped Retrieval
-- without requiring heavy JOINs with course_nodes during vector similarity search.

CREATE TABLE IF NOT EXISTS public.knowledge_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Link to the specific node in the skeleton
    node_id UUID NOT NULL REFERENCES public.course_nodes(id) ON DELETE CASCADE,
    
    -- Inherited path from parent node + chunk identifier
    -- e.g., 'root.unit1.lesson3.chunk_55'
    -- Allows: WHERE chunk_path <@ 'root.unit1' AND embedding <=> query < threshold
    chunk_path ltree NOT NULL,
    
    content TEXT NOT NULL,
    
    -- Vector embedding (Jina v3 / 1024d)
    embedding vector(1024),
    
    -- Token count for cost estimation / context window management
    token_count INTEGER,
    
    created_at TIMESTAMPTZ DEFAULT now(),
    
    -- Constraint: Ensure chunk path uniqueness
    CONSTRAINT uq_knowledge_chunk_path UNIQUE (chunk_path)
);

-- Indexes for knowledge_chunks

-- 1. GIST Index for Path Filtering (Scoped Retrieval)
-- This allows us to rapidly narrow down the search space BEFORE calculating cosine distance.
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_path_gist ON public.knowledge_chunks USING GIST(chunk_path);

-- 2. HNSW Index for Vector Search (Semantic Retrieval)
-- Hierarchical Navigable Small World graphs for Approximate Nearest Neighbor (ANN) search.
-- ef_construction=64: Good balance between build time and recall.
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_embedding 
ON public.knowledge_chunks USING hnsw (embedding vector_cosine_ops);

-- 3. Standard foreign key index
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_node_id ON public.knowledge_chunks(node_id);


-- 4. SECURITY (Row Level Security)
-- =============================================================================
-- Ensure strict data isolation between tenants/users.

-- Enable RLS
ALTER TABLE public.course_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.knowledge_chunks ENABLE ROW LEVEL SECURITY;

-- 4.1 Policies for course_nodes
-- Access control delegates to the parent 'courses' table.

DROP POLICY IF EXISTS "Course Owners/Admins can ALL on course_nodes" ON public.course_nodes;
CREATE POLICY "Course Owners/Admins can ALL on course_nodes"
ON public.course_nodes
FOR ALL
TO authenticated
USING (
    EXISTS (
        SELECT 1 FROM public.courses c
        WHERE c.id = public.course_nodes.course_id
        AND (c.teacher_id = auth.uid() OR public.is_admin())
    )
)
WITH CHECK (
    EXISTS (
        SELECT 1 FROM public.courses c
        WHERE c.id = public.course_nodes.course_id
        AND (c.teacher_id = auth.uid() OR public.is_admin())
    )
);

-- 4.2 Policies for knowledge_chunks
-- Access control delegates to the grandparent 'courses' table via 'course_nodes'.
-- Note: Doing a 2-level join in RLS can be expensive. 
-- Optimization: In a real high-scale scenario, we might denounce 'teacher_id' or 'course_id' to this table.
-- For now, we trust the query optimizer and the indexes.

DROP POLICY IF EXISTS "Course Owners/Admins can ALL on knowledge_chunks" ON public.knowledge_chunks;
CREATE POLICY "Course Owners/Admins can ALL on knowledge_chunks"
ON public.knowledge_chunks
FOR ALL
TO authenticated
USING (
    EXISTS (
        SELECT 1 FROM public.course_nodes cn
        JOIN public.courses c ON c.id = cn.course_id
        WHERE cn.id = public.knowledge_chunks.node_id
        AND (c.teacher_id = auth.uid() OR public.is_admin())
    )
)
WITH CHECK (
    EXISTS (
        SELECT 1 FROM public.course_nodes cn
        JOIN public.courses c ON c.id = cn.course_id
        WHERE cn.id = public.knowledge_chunks.node_id
        AND (c.teacher_id = auth.uid() OR public.is_admin())
    )
);

COMMIT;
