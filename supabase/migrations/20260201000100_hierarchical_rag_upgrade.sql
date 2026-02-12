-- Upgrade knowledge_chunks for Hybrid Search
ALTER TABLE public.knowledge_chunks ADD COLUMN IF NOT EXISTS fts tsvector;

-- Update existing rows (if any)
UPDATE public.knowledge_chunks SET fts = to_tsvector('spanish', content);

-- Trigger for auto-update FTS
CREATE OR REPLACE FUNCTION knowledge_chunks_fts_trigger() RETURNS trigger AS $$
BEGIN
  new.fts := to_tsvector('spanish', new.content);
  RETURN new;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_knowledge_chunks_fts ON public.knowledge_chunks;
CREATE TRIGGER trg_knowledge_chunks_fts
  BEFORE INSERT OR UPDATE OF content ON public.knowledge_chunks
  FOR EACH ROW EXECUTE FUNCTION knowledge_chunks_fts_trigger();

-- GIN Index for FTS
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_fts ON public.knowledge_chunks USING gin(fts);

-- Hierarchical Search RPC (Pure Node Architecture)
-- Searches knowledge_chunks joined with course_nodes for course isolation.
CREATE OR REPLACE FUNCTION hierarchical_search(
    query_text TEXT,
    query_embedding VECTOR(1536),
    match_threshold FLOAT,
    match_count INT,
    filter_course_id UUID,
    filter_path_prefix LTREE DEFAULT NULL
)
RETURNS TABLE (
    id UUID,
    node_id UUID,
    content TEXT,
    chunk_path LTREE,
    similarity FLOAT,
    fts_rank REAL,
    rrf_score FLOAT,
    node_title TEXT
)
LANGUAGE plpgsql
AS $$
DECLARE
    k CONSTANT INT := 60; -- RRF constant
BEGIN
    RETURN QUERY
    WITH vector_search AS (
        SELECT 
            kc.id,
            (1 - (kc.embedding <=> query_embedding)) AS similarity,
            ROW_NUMBER() OVER (ORDER BY (kc.embedding <=> query_embedding) ASC) AS rank_vec
        FROM public.knowledge_chunks kc
        JOIN public.course_nodes cn ON cn.id = kc.node_id
        WHERE cn.course_id = filter_course_id
        AND (filter_path_prefix IS NULL OR kc.chunk_path <@ filter_path_prefix)
        AND (1 - (kc.embedding <=> query_embedding)) > match_threshold
        ORDER BY similarity DESC
        LIMIT match_count * 2
    ),
    keyword_search AS (
        SELECT 
            kc.id,
            ts_rank_cd(kc.fts, websearch_to_tsquery('spanish', query_text)) AS rank_val,
            ROW_NUMBER() OVER (ORDER BY ts_rank_cd(kc.fts, websearch_to_tsquery('spanish', query_text)) DESC) AS rank_fts
        FROM public.knowledge_chunks kc
        JOIN public.course_nodes cn ON cn.id = kc.node_id
        WHERE cn.course_id = filter_course_id
        AND (filter_path_prefix IS NULL OR kc.chunk_path <@ filter_path_prefix)
        AND kc.fts @@ websearch_to_tsquery('spanish', query_text)
        ORDER BY rank_val DESC
        LIMIT match_count * 2
    )
    SELECT
        kc.id,
        kc.node_id,
        kc.content,
        kc.chunk_path,
        COALESCE(vs.similarity, 0) AS similarity,
        COALESCE(ks.rank_val, 0) AS fts_rank,
        (
            COALESCE(1.0 / (k + vs.rank_vec), 0.0) +
            COALESCE(1.0 / (k + ks.rank_fts), 0.0)
        ) AS rrf_score,
        cn.title as node_title
    FROM public.knowledge_chunks kc
    JOIN public.course_nodes cn ON cn.id = kc.node_id
    LEFT JOIN vector_search vs ON vs.id = kc.id
    LEFT JOIN keyword_search ks ON ks.id = kc.id
    WHERE vs.id IS NOT NULL OR ks.id IS NOT NULL
    ORDER BY rrf_score DESC
    LIMIT match_count;
END;
$$;
