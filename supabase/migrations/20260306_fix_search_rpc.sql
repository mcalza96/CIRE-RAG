-- Migration: 20260306_fix_search_rpc
-- Context: Fixing "column cc.metadata does not exist" error in atomic retrieval.

-- Redefine search_course_knowledge to construct metadata from existing columns.
CREATE OR REPLACE FUNCTION search_course_knowledge(
  query_text TEXT,
  query_embedding VECTOR(768), 
  match_threshold FLOAT,
  match_count INT,
  filter_course_id UUID
)
RETURNS TABLE (
  id UUID,
  content TEXT,
  similarity FLOAT,
  source_id UUID, -- Added source_id to return
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
      cc.source_id,
      jsonb_build_object(
        'semantic_context', cc.semantic_context,
        'page_number', cc.file_page_number
      ) as constructed_metadata,
      (1 - (cc.embedding <=> query_embedding)) as vector_score
    FROM content_chunks cc
    JOIN source_documents sd ON sd.id = cc.source_id
    WHERE sd.course_id = filter_course_id
    AND (1 - (cc.embedding <=> query_embedding)) > match_threshold
    ORDER BY vector_score DESC
    LIMIT match_count * 2
  ),
  keyword_search AS (
    SELECT
      cc.id,
      ts_rank_cd(to_tsvector('spanish', cc.content), plainto_tsquery('spanish', query_text)) as text_score
    FROM content_chunks cc
    JOIN source_documents sd ON sd.id = cc.source_id
    WHERE sd.course_id = filter_course_id
    AND to_tsvector('spanish', cc.content) @@ plainto_tsquery('spanish', query_text)
    ORDER BY text_score DESC
    LIMIT match_count * 2
  )
  SELECT
    COALESCE(v.id, k.id) as id,
    COALESCE(v.content, (SELECT content FROM content_chunks WHERE id = k.id)) as content,
    (
      COALESCE(v.vector_score, 0) * 0.7 + 
      LEAST(COALESCE(k.text_score, 0), 1.0) * 0.3
    ) as similarity,
    COALESCE(v.source_id, (SELECT source_id FROM content_chunks WHERE id = k.id)) as source_id,
    COALESCE(v.constructed_metadata, (
        SELECT jsonb_build_object(
            'semantic_context', semantic_context, 
            'page_number', file_page_number
        ) FROM content_chunks WHERE id = k.id
    )) as metadata
  FROM vector_search v
  FULL OUTER JOIN keyword_search k ON v.id = k.id
  ORDER BY similarity DESC
  LIMIT match_count;
END;
$$;
