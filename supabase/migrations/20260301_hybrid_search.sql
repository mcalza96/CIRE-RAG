-- Enable pgvector extension (idempotent)
CREATE EXTENSION IF NOT EXISTS vector;

-- Ensure indexes exist for performance
CREATE INDEX IF NOT EXISTS content_chunks_embedding_idx ON content_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS content_chunks_content_idx ON content_chunks USING gin (to_tsvector('spanish', content));

-- Hybrid Search Function (RPC)
-- Combines Vector Similarity (70%) + Keyword Matching (30%)
-- Uses a simplified RRF-like logic by normalizing rank or just weighted sum if scores are compatible.
-- Here we use a weighted sum of normalized scores for simplicity and performance.

CREATE OR REPLACE FUNCTION search_course_knowledge(
  query_text TEXT,
  query_embedding VECTOR(768), -- Dimensions for Google text-embedding-004
  match_threshold FLOAT,
  match_count INT,
  filter_course_id UUID
)
RETURNS TABLE (
  id UUID,
  content TEXT,
  similarity FLOAT,
  metadata JSONB
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  WITH vector_search AS (
    SELECT
      cc.id,
      cc.content,
      cc.metadata,
      (1 - (cc.embedding <=> query_embedding)) as vector_score
    FROM content_chunks cc
    WHERE cc.course_id = filter_course_id
    AND (1 - (cc.embedding <=> query_embedding)) > match_threshold
    ORDER BY vector_score DESC
    LIMIT match_count * 2 -- Fetch more for re-ranking/fusion
  ),
  keyword_search AS (
    SELECT
      cc.id,
      ts_rank_cd(to_tsvector('spanish', cc.content), plainto_tsquery('spanish', query_text)) as text_score
    FROM content_chunks cc
    WHERE cc.course_id = filter_course_id
    AND to_tsvector('spanish', cc.content) @@ plainto_tsquery('spanish', query_text)
    ORDER BY text_score DESC
    LIMIT match_count * 2
  )
  SELECT
    COALESCE(v.id, k.id) as id,
    COALESCE(v.content, (SELECT content FROM content_chunks WHERE id = k.id)) as content,
    -- Hybrid Score Calculation:
    -- We assume vector_score is 0-1. text_score is unbounded, so we normalize effectively by weighting.
    -- A simple approach: Max(vector_score, normalized_text_score) or just vector_score if text match exists boost.
    (
      COALESCE(v.vector_score, 0) * 0.7 + 
      LEAST(COALESCE(k.text_score, 0), 1.0) * 0.3 -- Cap text score impact
    ) as similarity,
    COALESCE(v.metadata, (SELECT metadata FROM content_chunks WHERE id = k.id)) as metadata
  FROM vector_search v
  FULL OUTER JOIN keyword_search k ON v.id = k.id
  ORDER BY similarity DESC
  LIMIT match_count;
END;
$$;
