-- Migration: Allow Staff (Teachers/Instructors) to view Global Ingestion Logs
-- Date: 2026-02-09

BEGIN;

-- Policy: Staff can view global ingestion logs (tenant_id IS NULL)
CREATE POLICY "Staff can view global ingestion logs"
ON public.ingestion_events
FOR SELECT
TO authenticated
USING (
    tenant_id IS NULL
    AND public.is_staff()
);

COMMIT;
