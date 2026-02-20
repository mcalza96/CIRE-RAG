-- Migration: Robust scoped hybrid retrieval with exact-token channel.
-- - Adds simple-config exact FTS index for alphanumeric identifiers.
-- - Reworks retrieve_hybrid_optimized to scope server-side (tenant/global/collection/standard)
--   and fuse vector + FTS(es) + FTS(simple) + literal identifier hits using weighted RRF.

ALTER TABLE public.content_chunks
  ADD COLUMN IF NOT EXISTS fts_exact tsvector
  GENERATED ALWAYS AS (
    to_tsvector('simple', coalesce(content, '') || ' ' || coalesce(semantic_context, ''))
  ) STORED;

CREATE INDEX IF NOT EXISTS idx_content_chunks_fts_exact
ON public.content_chunks USING gin (fts_exact);

CREATE OR REPLACE FUNCTION public.retrieve_hybrid_optimized(
  query_embedding vector,
  query_text text DEFAULT NULL,
  match_threshold double precision DEFAULT 0.25,
  match_count integer DEFAULT 40,
  rrf_k integer DEFAULT 60,
  vector_weight double precision DEFAULT 0.7,
  fts_weight double precision DEFAULT 0.3,
  hnsw_ef_search integer DEFAULT 80,
  tenant_id uuid DEFAULT NULL,
  is_global boolean DEFAULT NULL,
  collection_id uuid DEFAULT NULL,
  source_standard text DEFAULT NULL,
  source_standards text[] DEFAULT NULL
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
WITH _cfg AS (
  SELECT set_config('hnsw.ef_search', GREATEST(hnsw_ef_search, 10)::text, true)
),
query_traits AS (
  SELECT
    coalesce(trim(query_text), '') AS cleaned_query,
    (coalesce(trim(query_text), '') ~* '([A-Za-z]+[-./]?[0-9]+|[0-9]+[A-Za-z]+|iso\s*[0-9]{3,5}|cl[aá]usula\s*[0-9]+(?:\.[0-9]+)*)') AS identifier_mode
),
normalized_scope AS (
  SELECT
    ARRAY(
      SELECT DISTINCT lower(trim(v))
      FROM unnest(
        coalesce(source_standards, ARRAY[]::text[])
        || CASE WHEN coalesce(trim(source_standard), '') <> '' THEN ARRAY[source_standard] ELSE ARRAY[]::text[] END
      ) AS v
      WHERE coalesce(trim(v), '') <> ''
    ) AS scope_terms
),
weight_cfg AS (
  SELECT
    greatest(vector_weight, 0.0) AS vector_w,
    CASE WHEN qt.identifier_mode THEN least(greatest(fts_weight, 0.0), 0.2) ELSE greatest(fts_weight, 0.0) END AS fts_es_w,
    CASE WHEN qt.identifier_mode THEN greatest(fts_weight * 1.6, 0.45) ELSE least(greatest(fts_weight, 0.0), 0.2) END AS fts_exact_w,
    CASE WHEN qt.identifier_mode THEN 0.35 ELSE 0.05 END AS literal_w,
    qt.cleaned_query,
    qt.identifier_mode
  FROM query_traits qt
),
candidate_chunks AS (
  SELECT
    cc.id,
    cc.content,
    cc.semantic_context,
    cc.embedding,
    cc.fts,
    cc.fts_exact,
    cc.file_page_number,
    cc.source_id,
    lower(
      trim(
        coalesce(
          sd.metadata->>'source_standard',
          sd.metadata->>'standard',
          sd.metadata->>'scope',
          sd.metadata->>'norma',
          ''
        )
      )
    ) AS row_scope
  FROM public.content_chunks cc
  JOIN public.source_documents sd ON sd.id = cc.source_id
  CROSS JOIN normalized_scope ns
  WHERE
    (retrieve_hybrid_optimized.tenant_id IS NULL OR sd.institution_id = retrieve_hybrid_optimized.tenant_id)
    AND (retrieve_hybrid_optimized.is_global IS NULL OR sd.is_global = retrieve_hybrid_optimized.is_global)
    AND (retrieve_hybrid_optimized.collection_id IS NULL OR sd.collection_id = retrieve_hybrid_optimized.collection_id)
    AND (
      coalesce(array_length(ns.scope_terms, 1), 0) = 0
      OR (
        coalesce(
          lower(
            trim(
              coalesce(
                sd.metadata->>'source_standard',
                sd.metadata->>'standard',
                sd.metadata->>'scope',
                sd.metadata->>'norma',
                ''
              )
            )
          ),
          ''
        ) <> ''
        AND EXISTS (
          SELECT 1
          FROM unnest(ns.scope_terms) AS s
          WHERE
            lower(
              trim(
                coalesce(
                  sd.metadata->>'source_standard',
                  sd.metadata->>'standard',
                  sd.metadata->>'scope',
                  sd.metadata->>'norma',
                  ''
                )
              )
            ) = s
            OR lower(
              trim(
                coalesce(
                  sd.metadata->>'source_standard',
                  sd.metadata->>'standard',
                  sd.metadata->>'scope',
                  sd.metadata->>'norma',
                  ''
                )
              )
            ) LIKE ('%' || s || '%')
        )
      )
    )
),
vector_hits AS (
  SELECT
    c.id,
    c.content,
    jsonb_build_object(
      'semantic_context', c.semantic_context,
      'file_page_number', c.file_page_number,
      'source_id', c.source_id,
      'source_standard', nullif(c.row_scope, '')
    ) AS metadata,
    (1 - (c.embedding <=> query_embedding))::double precision AS similarity
  FROM candidate_chunks c
  WHERE c.embedding IS NOT NULL
    AND (1 - (c.embedding <=> query_embedding)) > match_threshold
  ORDER BY similarity DESC
  LIMIT GREATEST(match_count, 1)
),
fts_es_hits AS (
  SELECT
    c.id,
    c.content,
    jsonb_build_object(
      'semantic_context', c.semantic_context,
      'file_page_number', c.file_page_number,
      'source_id', c.source_id,
      'source_standard', nullif(c.row_scope, '')
    ) AS metadata,
    ts_rank_cd(c.fts, websearch_to_tsquery('spanish', wc.cleaned_query))::double precision AS rank
  FROM candidate_chunks c
  CROSS JOIN weight_cfg wc
  WHERE wc.cleaned_query <> ''
    AND c.fts @@ websearch_to_tsquery('spanish', wc.cleaned_query)
  ORDER BY rank DESC
  LIMIT GREATEST(match_count, 1)
),
fts_exact_hits AS (
  SELECT
    c.id,
    c.content,
    jsonb_build_object(
      'semantic_context', c.semantic_context,
      'file_page_number', c.file_page_number,
      'source_id', c.source_id,
      'source_standard', nullif(c.row_scope, '')
    ) AS metadata,
    ts_rank_cd(c.fts_exact, websearch_to_tsquery('simple', wc.cleaned_query))::double precision AS rank
  FROM candidate_chunks c
  CROSS JOIN weight_cfg wc
  WHERE wc.cleaned_query <> ''
    AND c.fts_exact @@ websearch_to_tsquery('simple', wc.cleaned_query)
  ORDER BY rank DESC
  LIMIT GREATEST(match_count, 1)
),
literal_hits AS (
  SELECT
    c.id,
    c.content,
    jsonb_build_object(
      'semantic_context', c.semantic_context,
      'file_page_number', c.file_page_number,
      'source_id', c.source_id,
      'source_standard', nullif(c.row_scope, '')
    ) AS metadata,
    1.0::double precision AS rank
  FROM candidate_chunks c
  CROSS JOIN weight_cfg wc
  WHERE wc.identifier_mode
    AND wc.cleaned_query <> ''
    AND (
      c.content ILIKE ('%' || wc.cleaned_query || '%')
      OR coalesce(c.semantic_context, '') ILIKE ('%' || wc.cleaned_query || '%')
      OR coalesce(c.row_scope, '') ILIKE ('%' || wc.cleaned_query || '%')
    )
  LIMIT GREATEST(match_count, 1)
),
vector_ranked AS (
  SELECT
    vh.id,
    vh.content,
    vh.metadata,
    vh.similarity,
    row_number() OVER (ORDER BY vh.similarity DESC) AS rank_pos
  FROM vector_hits vh
),
fts_es_ranked AS (
  SELECT
    fh.id,
    fh.content,
    fh.metadata,
    fh.rank,
    row_number() OVER (ORDER BY fh.rank DESC) AS rank_pos
  FROM fts_es_hits fh
),
fts_exact_ranked AS (
  SELECT
    fx.id,
    fx.content,
    fx.metadata,
    fx.rank,
    row_number() OVER (ORDER BY fx.rank DESC) AS rank_pos
  FROM fts_exact_hits fx
),
literal_ranked AS (
  SELECT
    lh.id,
    lh.content,
    lh.metadata,
    lh.rank,
    row_number() OVER (ORDER BY lh.rank DESC) AS rank_pos
  FROM literal_hits lh
),
fused AS (
  SELECT
    coalesce(v.id, e.id, x.id, l.id) AS id,
    coalesce(v.content, e.content, x.content, l.content) AS content,
    coalesce(v.metadata, e.metadata, x.metadata, l.metadata) AS metadata,
    greatest(
      coalesce(v.similarity, 0.0),
      coalesce(e.rank, 0.0),
      coalesce(x.rank, 0.0),
      coalesce(l.rank, 0.0)
    )::double precision AS similarity,
    (
      coalesce((SELECT vector_w FROM weight_cfg) / (rrf_k + v.rank_pos), 0.0)
      + coalesce((SELECT fts_es_w FROM weight_cfg) / (rrf_k + e.rank_pos), 0.0)
      + coalesce((SELECT fts_exact_w FROM weight_cfg) / (rrf_k + x.rank_pos), 0.0)
      + coalesce((SELECT literal_w FROM weight_cfg) / (rrf_k + l.rank_pos), 0.0)
    )::double precision AS score
  FROM vector_ranked v
  FULL OUTER JOIN fts_es_ranked e ON e.id = v.id
  FULL OUTER JOIN fts_exact_ranked x ON x.id = coalesce(v.id, e.id)
  FULL OUTER JOIN literal_ranked l ON l.id = coalesce(v.id, e.id, x.id)
)
SELECT
  f.id,
  f.content,
  f.metadata,
  f.similarity,
  f.score,
  'hybrid'::text AS source_layer,
  'content_chunk'::text AS source_type
FROM fused f
WHERE f.id IS NOT NULL
ORDER BY f.score DESC, f.similarity DESC
LIMIT GREATEST(match_count, 1);
$$;
