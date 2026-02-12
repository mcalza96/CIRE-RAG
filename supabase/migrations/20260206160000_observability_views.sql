-- Add tenant_id to ai_audit_logs for Cost Attribution
ALTER TABLE "public"."ai_audit_logs" ADD COLUMN IF NOT EXISTS "tenant_id" uuid;
CREATE INDEX IF NOT EXISTS ai_audit_logs_tenant_id_idx ON "public"."ai_audit_logs" (tenant_id);

-- 1. Latency Metrics (Hourly)
CREATE OR REPLACE VIEW metrics_latency_hourly AS
SELECT
    date_trunc('hour', created_at) as hour,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms) as p95_ms,
    PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY latency_ms) as p99_ms,
    AVG(latency_ms) as avg_ms,
    COUNT(*) as request_count,
    COUNT(*) FILTER (WHERE status = 'failed') as error_count
FROM ai_audit_logs
WHERE created_at > (now() - interval '7 days')
GROUP BY 1
ORDER BY 1 DESC;

-- 2. Tenant Cost (Daily)
-- Assumes tokens_used is { "prompt": 100, "completion": 50, "total": 150 }
CREATE OR REPLACE VIEW metrics_tenant_cost_daily AS
SELECT
    date_trunc('day', created_at) as day,
    tenant_id,
    SUM(COALESCE((tokens_used->>'total')::int, 0)) as total_tokens,
    COUNT(*) as request_count
FROM ai_audit_logs
WHERE tenant_id IS NOT NULL AND created_at > (now() - interval '30 days')
GROUP BY 1, 2
ORDER BY 1 DESC;

-- 3. RAG Health (Hourly)
-- Tracks Miss Rate (0 chunks retrieved)
CREATE OR REPLACE VIEW metrics_rag_health_hourly AS
SELECT
    date_trunc('hour', created_at) as hour,
    COUNT(*) as total_requests,
    COUNT(*) FILTER (WHERE (retrieval_stats->>'totalChunks')::int = 0) as zero_retrievals,
    ROUND(
        (COUNT(*) FILTER (WHERE (retrieval_stats->>'totalChunks')::int = 0)::numeric / GREATEST(COUNT(*), 1)) * 100, 
        2
    ) as miss_rate_percent
FROM ai_audit_logs
WHERE created_at > (now() - interval '7 days')
GROUP BY 1
ORDER BY 1 DESC;

-- 4. Human Intervention (Daily)
-- Joins logs with feedback/edits to measure "Quality Drift"
CREATE OR REPLACE VIEW metrics_human_intervention_daily AS
SELECT
    date_trunc('day', created_at) as day,
    AVG(edit_distance) as avg_edit_distance,
    AVG(similarity_score) as avg_similarity,
    COUNT(*) as feedback_count
FROM ai_feedback_dataset
WHERE created_at > (now() - interval '30 days')
GROUP BY 1
ORDER BY 1 DESC;
