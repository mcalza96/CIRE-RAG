-- Migration: RAG Validator RPC
-- Description: Adds 'hybrid_search_document' for inspecting specific files.
-- Date: 2026-02-05

CREATE OR REPLACE FUNCTION public.hybrid_search_document(
  query_text text,
  query_embedding vector,
  match_threshold double precision,
  match_count integer,
  filter_source_id uuid
)
RETURNS TABLE(
  id uuid,
  content text,
  semantic_context text,
  file_page_number integer,
  similarity double precision,
  fts_rank double precision,
  rrf_score double precision
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'extensions'
AS $function$
DECLARE
    k CONSTANT INT := 60;
BEGIN
    RETURN QUERY
    WITH vector_search AS (
        SELECT 
            cc.id,
            (1 - (cc.embedding <=> query_embedding)) AS similarity,
            ROW_NUMBER() OVER (ORDER BY (cc.embedding <=> query_embedding) ASC) AS rank_vec
        FROM public.content_chunks cc
        WHERE cc.source_id = filter_source_id
        AND (1 - (cc.embedding <=> query_embedding)) > match_threshold
        ORDER BY similarity DESC
        LIMIT match_count * 2
    ),
    keyword_search AS (
        SELECT 
            cc.id,
            ts_rank_cd(cc.fts, websearch_to_tsquery('spanish', query_text)) AS rank_val,
            ROW_NUMBER() OVER (ORDER BY ts_rank_cd(cc.fts, websearch_to_tsquery('spanish', query_text)) DESC) AS rank_fts
        FROM public.content_chunks cc
        WHERE cc.source_id = filter_source_id
        AND cc.fts @@ websearch_to_tsquery('spanish', query_text)
        ORDER BY rank_val DESC
        LIMIT match_count * 2
    )
    SELECT
        cc.id,
        cc.content,
        cc.semantic_context,
        cc.file_page_number,
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
$function$;
