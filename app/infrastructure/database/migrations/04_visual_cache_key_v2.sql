-- =============================================================================
-- MIGRATION: Visual Cache Key v2 Dimensions
-- Purpose:
--   Harden cache key to avoid stale reuse across prompt/schema/content-type changes.
-- =============================================================================

BEGIN;

ALTER TABLE public.cache_visual_extractions
    ADD COLUMN IF NOT EXISTS content_type VARCHAR(32) NOT NULL DEFAULT 'table',
    ADD COLUMN IF NOT EXISTS prompt_version VARCHAR(32) NOT NULL DEFAULT 'v1',
    ADD COLUMN IF NOT EXISTS schema_version VARCHAR(64) NOT NULL DEFAULT 'VisualParseResult:v1';

-- Drop previous PK if present and replace with v2 dimensions.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'cache_visual_extractions_pk'
          AND conrelid = 'public.cache_visual_extractions'::regclass
    ) THEN
        ALTER TABLE public.cache_visual_extractions
            DROP CONSTRAINT cache_visual_extractions_pk;
    END IF;
END $$;

ALTER TABLE public.cache_visual_extractions
    ADD CONSTRAINT cache_visual_extractions_pk
    PRIMARY KEY (
        image_hash,
        provider,
        model_version,
        content_type,
        prompt_version,
        schema_version
    );

CREATE INDEX IF NOT EXISTS idx_cache_visual_extractions_lookup_v2
    ON public.cache_visual_extractions (
        provider,
        model_version,
        content_type,
        prompt_version,
        schema_version,
        created_at DESC
    );

COMMIT;
