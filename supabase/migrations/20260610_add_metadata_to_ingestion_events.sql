BEGIN;

ALTER TABLE public.ingestion_events
ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_ingestion_events_metadata_gin
ON public.ingestion_events USING gin (metadata);

COMMIT;
