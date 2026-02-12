-- Batch hybrid retrieval for SParC-RAG (Vector + FTS + RRF)
-- Returns top-N results per query variant in a single RPC call.

CREATE OR REPLACE FUNCTION public.hierarchical_search_batch(
    query_texts text[],
    query_embeddings vector(1536)[],
    match_threshold float,
    match_count int,
    filter_course_id uuid,
    filter_path_prefix ltree DEFAULT NULL
)
RETURNS TABLE (
    query_id int,
    id uuid,
    node_id uuid,
    content text,
    chunk_path ltree,
    similarity float,
    fts_rank real,
    rrf_score float,
    node_title text
)
LANGUAGE plpgsql
AS $$
DECLARE
    rrf_k CONSTANT int := 60;
    text_count int := COALESCE(array_length(query_texts, 1), 0);
    emb_count int := COALESCE(array_length(query_embeddings, 1), 0);
BEGIN
    IF text_count <> emb_count THEN
        RAISE EXCEPTION
            'query_texts (%) and query_embeddings (%) must have the same length',
            text_count, emb_count
            USING ERRCODE = '2202E';
    END IF;

    IF text_count = 0 OR match_count <= 0 THEN
        RETURN;
    END IF;

    RETURN QUERY
    WITH input_queries AS (
        SELECT
            t.ord::int AS query_id,
            t.query_text,
            e.query_embedding,
            CASE
                WHEN NULLIF(btrim(t.query_text), '') IS NOT NULL
                THEN websearch_to_tsquery('spanish', t.query_text)
                ELSE NULL
            END AS tsq
        FROM unnest(query_texts) WITH ORDINALITY AS t(query_text, ord)
        JOIN unnest(query_embeddings) WITH ORDINALITY AS e(query_embedding, ord)
          ON e.ord = t.ord
    ),
    vector_hits AS (
        SELECT
            iq.query_id,
            v.id,
            v.similarity,
            v.rank_vec
        FROM input_queries iq
        CROSS JOIN LATERAL (
            SELECT
                kc.id,
                (1 - (kc.embedding <=> iq.query_embedding))::float AS similarity,
                row_number() OVER (ORDER BY (kc.embedding <=> iq.query_embedding) ASC) AS rank_vec
            FROM public.knowledge_chunks kc
            JOIN public.course_nodes cn ON cn.id = kc.node_id
            WHERE cn.course_id = filter_course_id
              AND (filter_path_prefix IS NULL OR kc.chunk_path <@ filter_path_prefix)
              AND (1 - (kc.embedding <=> iq.query_embedding)) > match_threshold
            ORDER BY (kc.embedding <=> iq.query_embedding) ASC
            LIMIT GREATEST(match_count * 2, 1)
        ) v
    ),
    keyword_hits AS (
        SELECT
            iq.query_id,
            k.id,
            k.fts_rank,
            k.rank_fts
        FROM input_queries iq
        CROSS JOIN LATERAL (
            SELECT
                kc.id,
                ts_rank_cd(kc.fts, iq.tsq) AS fts_rank,
                row_number() OVER (ORDER BY ts_rank_cd(kc.fts, iq.tsq) DESC) AS rank_fts
            FROM public.knowledge_chunks kc
            JOIN public.course_nodes cn ON cn.id = kc.node_id
            WHERE iq.tsq IS NOT NULL
              AND cn.course_id = filter_course_id
              AND (filter_path_prefix IS NULL OR kc.chunk_path <@ filter_path_prefix)
              AND kc.fts @@ iq.tsq
            ORDER BY ts_rank_cd(kc.fts, iq.tsq) DESC
            LIMIT GREATEST(match_count * 2, 1)
        ) k
    ),
    fused AS (
        SELECT
            COALESCE(v.query_id, k.query_id) AS query_id,
            COALESCE(v.id, k.id) AS id,
            v.similarity,
            k.fts_rank,
            (
                COALESCE(1.0 / (rrf_k + v.rank_vec), 0.0) +
                COALESCE(1.0 / (rrf_k + k.rank_fts), 0.0)
            )::float AS rrf_score
        FROM vector_hits v
        FULL OUTER JOIN keyword_hits k
          ON k.query_id = v.query_id
         AND k.id = v.id
    ),
    ranked AS (
        SELECT
            f.query_id,
            kc.id,
            kc.node_id,
            kc.content,
            kc.chunk_path,
            COALESCE(f.similarity, 0)::float AS similarity,
            COALESCE(f.fts_rank, 0)::real AS fts_rank,
            f.rrf_score,
            cn.title AS node_title,
            row_number() OVER (
                PARTITION BY f.query_id
                ORDER BY f.rrf_score DESC, kc.id
            ) AS rn
        FROM fused f
        JOIN public.knowledge_chunks kc ON kc.id = f.id
        JOIN public.course_nodes cn ON cn.id = kc.node_id
    )
    SELECT
        ranked.query_id,
        ranked.id,
        ranked.node_id,
        ranked.content,
        ranked.chunk_path,
        ranked.similarity,
        ranked.fts_rank,
        ranked.rrf_score,
        ranked.node_title
    FROM ranked
    WHERE ranked.rn <= match_count
    ORDER BY ranked.query_id, ranked.rrf_score DESC;
END;
$$;
