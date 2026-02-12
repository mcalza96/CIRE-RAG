-- =============================================================================
-- MIGRATION: RAG INFRASTRUCTURE (Course-Scoped)
-- Description: Adds tables for storing source documents and vector embeddings.
--              Includes RLS policies and Hybrid Search (RRF) RPC.
-- Date: 2026-02-01
-- =============================================================================

BEGIN;

-- 1. EXTENSIONS
-- Enable pgvector if not already enabled
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

-- 2. TABLES

-- 2.1 source_documents
-- Stores metadata for uploaded files (PDFs, etc.) linked to a course.
CREATE TABLE IF NOT EXISTS public.source_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id UUID NOT NULL REFERENCES public.courses(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    grade_level TEXT, -- Optional pedagogical metadata
    subject TEXT,     -- Optional pedagogical metadata
    metadata JSONB DEFAULT '{}'::jsonb, -- Flexible metadata (author, year, etc.)
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Index for faster lookups by course
CREATE INDEX IF NOT EXISTS idx_source_documents_course_id ON public.source_documents(course_id);

-- 2.2 content_chunks
-- Stores text fragments and their vector embeddings.
CREATE TABLE IF NOT EXISTS public.content_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID NOT NULL REFERENCES public.source_documents(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    semantic_context TEXT, -- Context extracted from document hierarchy (titles, headers)
    embedding vector(1536), -- Compatible with text-embedding-3-large (truncated)
    fts tsvector GENERATED ALWAYS AS (to_tsvector('spanish', content || ' ' || coalesce(semantic_context, ''))) STORED,
    file_page_number INTEGER, -- Page number in original PDF
    chunk_index INTEGER, -- Ordering within the document
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_content_chunks_source_id ON public.content_chunks(source_id);

-- HNSW Index for Vector Search (Cosine Distance)
-- Note: ef_construction=64 is a balanced default.
CREATE INDEX IF NOT EXISTS idx_content_chunks_embedding 
ON public.content_chunks USING hnsw (embedding vector_cosine_ops);

-- GIN Index for Full Text Search
CREATE INDEX IF NOT EXISTS idx_content_chunks_fts 
ON public.content_chunks USING gin (fts);

-- 3. SECURITY (RLS)

-- 3.1 Enable RLS
ALTER TABLE public.source_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.content_chunks ENABLE ROW LEVEL SECURITY;

-- 3.2 Policies for source_documents
-- Only the teacher who owns the course (or admin) can view/manage documents.

CREATE POLICY "Course owners can manage source_documents"
ON public.source_documents
FOR ALL
TO authenticated
USING (
    EXISTS (
        SELECT 1 FROM public.courses c
        WHERE c.id = public.source_documents.course_id
        AND (c.teacher_id = auth.uid() OR public.is_admin())
    )
)
WITH CHECK (
    EXISTS (
        SELECT 1 FROM public.courses c
        WHERE c.id = public.source_documents.course_id
        AND (c.teacher_id = auth.uid() OR public.is_admin())
    )
);

-- 3.3 Policies for content_chunks
-- Inherit access from source_documents.

CREATE POLICY "Course owners can manage content_chunks"
ON public.content_chunks
FOR ALL
TO authenticated
USING (
    EXISTS (
        SELECT 1 FROM public.source_documents sd
        JOIN public.courses c ON c.id = sd.course_id
        WHERE sd.id = public.content_chunks.source_id
        AND (c.teacher_id = auth.uid() OR public.is_admin())
    )
)
WITH CHECK (
    EXISTS (
        SELECT 1 FROM public.source_documents sd
        JOIN public.courses c ON c.id = sd.course_id
        WHERE sd.id = public.content_chunks.source_id
        AND (c.teacher_id = auth.uid() OR public.is_admin())
    )
);

-- 4. FUNCTIONS (RPC)

-- 4.1 Hybrid Search using RRF (Reciprocal Rank Fusion)
-- Combines vector similarity (Cosine) and Keyword matching (FTS).
CREATE OR REPLACE FUNCTION public.hybrid_search(
    query_text TEXT,
    query_embedding vector(1536),
    match_threshold FLOAT,
    match_count INT,
    filter_course_id UUID
)
RETURNS TABLE (
    id UUID,
    source_id UUID,
    content TEXT,
    semantic_context TEXT,
    similarity FLOAT,
    fts_rank FLOAT,
    rrf_score FLOAT
)
LANGUAGE plpgsql
AS $$
DECLARE
    k CONSTANT INT := 60; -- RRF constant
BEGIN
    RETURN QUERY
    WITH vector_search AS (
        SELECT 
            cc.id,
            (1 - (cc.embedding <=> query_embedding)) AS similarity,
            ROW_NUMBER() OVER (ORDER BY (cc.embedding <=> query_embedding) ASC) AS rank_vec
        FROM public.content_chunks cc
        JOIN public.source_documents sd ON sd.id = cc.source_id
        WHERE sd.course_id = filter_course_id
        AND (1 - (cc.embedding <=> query_embedding)) > match_threshold
        ORDER BY similarity DESC
        LIMIT match_count * 2 -- Fetch more to allow for intersection
    ),
    keyword_search AS (
        SELECT 
            cc.id,
            ts_rank_cd(cc.fts, websearch_to_tsquery('spanish', query_text)) AS rank_val,
            ROW_NUMBER() OVER (ORDER BY ts_rank_cd(cc.fts, websearch_to_tsquery('spanish', query_text)) DESC) AS rank_fts
        FROM public.content_chunks cc
        JOIN public.source_documents sd ON sd.id = cc.source_id
        WHERE sd.course_id = filter_course_id
        AND cc.fts @@ websearch_to_tsquery('spanish', query_text)
        ORDER BY rank_val DESC
        LIMIT match_count * 2
    )
    SELECT
        cc.id,
        cc.source_id,
        cc.content,
        cc.semantic_context,
        COALESCE(vs.similarity, 0) AS similarity,
        COALESCE(ks.rank_val, 0) AS fts_rank,
        (
            COALESCE(1.0 / (k + vs.rank_vec), 0.0) +
            COALESCE(1.0 / (k + ks.rank_fts), 0.0)
        ) AS rrf_score
    FROM public.content_chunks cc
    LEFT JOIN vector_search vs ON vs.id = cc.id
    LEFT JOIN keyword_search ks ON ks.id = cc.id
    WHERE vs.id IS NOT NULL OR ks.id IS NOT NULL
    ORDER BY rrf_score DESC
    LIMIT match_count;
END;
$$ SECURITY DEFINER SET search_path = public, extensions;

-- Grant execute permission to authenticated users
GRANT EXECUTE ON FUNCTION public.hybrid_search(TEXT, vector(1536), FLOAT, INT, UUID) TO authenticated;
GRANT EXECUTE ON FUNCTION public.hybrid_search(TEXT, vector(1536), FLOAT, INT, UUID) TO service_role;

COMMIT;
