-- =============================================================================
-- MIGRATION: Visual Extraction Cache
-- Purpose:
--   Persist deterministic VLM extraction outputs keyed by image bytes hash.
--   This avoids repeated vision API calls for identical images.
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS public.cache_visual_extractions (
    image_hash VARCHAR(64) NOT NULL,
    provider VARCHAR(64) NOT NULL,
    model_version VARCHAR(128) NOT NULL,
    result_data JSONB NOT NULL,
    token_usage JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT cache_visual_extractions_pk PRIMARY KEY (image_hash, provider, model_version)
);

-- Supports daily budget-guard checks with low-latency date filtering.
CREATE INDEX IF NOT EXISTS idx_cache_visual_extractions_created_at
    ON public.cache_visual_extractions (created_at DESC);

-- Optional JSONB index for analytics and future cost dashboards.
CREATE INDEX IF NOT EXISTS idx_cache_visual_extractions_token_usage_gin
    ON public.cache_visual_extractions
    USING gin (token_usage)
    WHERE token_usage IS NOT NULL;

COMMIT;
