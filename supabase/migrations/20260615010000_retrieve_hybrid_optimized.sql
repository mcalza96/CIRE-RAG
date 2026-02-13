-- Migration: Hybrid super-query RPC
-- Description: Executes vector + FTS ranking and RRF fusion in one SQL RPC.

CREATE OR REPLACE FUNCTION public.retrieve_hybrid_optimized(
  query_embedding vector,
  query_text text,
  source_ids uuid[],
  match_threshold double precision DEFAULT 0.25,
  match_count integer DEFAULT 40,
  rrf_k integer DEFAULT 60,
  vector_weight double precision DEFAULT 0.7,
  fts_weight double precision DEFAULT 0.3
)
RETURNS TABLE(
  id uuid,
  content text,
  metadata jsonb,
  similarity double precision,
  score double precision,
  source_layer text,
  source_type text
)
LANGUAGE sql
SECURITY DEFINER
SET search_path TO 'public', 'extensions'
AS $$
WITH vector_hits AS (
  SELECT
    cc.id,
    cc.content,
    jsonb_build_object(
      'semantic_context', cc.semantic_context,
      'file_page_number', cc.file_page_number,
      'source_id', cc.source_id
    ) AS metadata,
    (1 - (cc.embedding <=> query_embedding))::double precision AS similarity
  FROM public.content_chunks cc
  WHERE cc.source_id = ANY(source_ids)
    AND (1 - (cc.embedding <=> query_embedding)) > match_threshold
  ORDER BY similarity DESC
  LIMIT GREATEST(match_count, 1)
),
fts_hits AS (
  SELECT
    cc.id,
    cc.content,
    jsonb_build_object(
      'semantic_context', cc.semantic_context,
      'file_page_number', cc.file_page_number,
      'source_id', cc.source_id
    ) AS metadata,
    ts_rank_cd(cc.fts, websearch_to_tsquery('spanish', query_text))::double precision AS rank
  FROM public.content_chunks cc
  WHERE cc.source_id = ANY(source_ids)
    AND COALESCE(trim(query_text), '') <> ''
    AND cc.fts @@ websearch_to_tsquery('spanish', query_text)
  ORDER BY rank DESC
  LIMIT GREATEST(match_count, 1)
),
vector_ranked AS (
  SELECT
    vh.id,
    vh.content,
    vh.metadata,
    vh.similarity,
    ROW_NUMBER() OVER (ORDER BY vh.similarity DESC) AS rank_pos
  FROM vector_hits vh
),
fts_ranked AS (
  SELECT
    fh.id,
    fh.content,
    fh.metadata,
    fh.rank,
    ROW_NUMBER() OVER (ORDER BY fh.rank DESC) AS rank_pos
  FROM fts_hits fh
),
fused AS (
  SELECT
    COALESCE(v.id, f.id) AS id,
    COALESCE(v.content, f.content) AS content,
    COALESCE(v.metadata, f.metadata) AS metadata,
    GREATEST(COALESCE(v.similarity, 0.0), COALESCE(f.rank, 0.0))::double precision AS similarity,
    (
      COALESCE(vector_weight / (rrf_k + v.rank_pos), 0.0)
      + COALESCE(fts_weight / (rrf_k + f.rank_pos), 0.0)
    )::double precision AS score
  FROM vector_ranked v
  FULL OUTER JOIN fts_ranked f ON f.id = v.id
)
SELECT
  fused.id,
  fused.content,
  fused.metadata,
  fused.similarity,
  fused.score,
  'hybrid'::text AS source_layer,
  'content_chunk'::text AS source_type
FROM fused
WHERE fused.id IS NOT NULL
ORDER BY fused.score DESC, fused.similarity DESC
LIMIT GREATEST(match_count, 1);
$$;
