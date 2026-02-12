-- Migration: Add tenant_id to ingestion_events for RAG Observability
-- Date: 2026-02-04

BEGIN;

-- 1. Add tenant_id column
ALTER TABLE public.ingestion_events 
ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES public.institutions(id) ON DELETE CASCADE;

-- 2. Create index for faster filtering
CREATE INDEX IF NOT EXISTS idx_ingestion_events_tenant_id ON public.ingestion_events(tenant_id);

-- 3. Backfill tenant_id from source_documents
UPDATE public.ingestion_events ie
SET tenant_id = sd.institution_id
FROM public.source_documents sd
WHERE ie.source_document_id = sd.id
AND ie.tenant_id IS NULL;

-- 4. Update RLS policies
DROP POLICY IF EXISTS "Allow authenticated read access" ON public.ingestion_events;
DROP POLICY IF EXISTS "Global Admins see all ingestion logs" ON public.ingestion_events;
DROP POLICY IF EXISTS "Tenant Admins see their ingestion logs" ON public.ingestion_events;

-- Policy A: Global Admins can see ALL logs
CREATE POLICY "Global Admins see all ingestion logs" 
ON public.ingestion_events
FOR SELECT
TO authenticated
USING (public.is_admin());

-- Policy B: Institutional Admins can see logs for THEIR tenant
CREATE POLICY "Tenant Admins see their ingestion logs" 
ON public.ingestion_events
FOR SELECT
TO authenticated
USING (
    tenant_id IS NOT NULL 
    AND 
    EXISTS (
        SELECT 1 FROM public.memberships m
        WHERE m.institution_id = public.ingestion_events.tenant_id
        AND m.user_id = auth.uid()
        AND m.role = 'admin'
    )
);

COMMIT;
