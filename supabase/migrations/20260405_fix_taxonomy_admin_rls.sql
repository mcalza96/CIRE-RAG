BEGIN;

-- 1. Fix: Add Admin Override Policy for document_taxonomy
-- The existing policy enforces a join with 'courses', which fails for Global Content (NULL course_id).
-- We need a separate, simple policy for admins to manage EVERYTHING in this table.

DROP POLICY IF EXISTS "Admins can manage all taxonomy entries" ON public.document_taxonomy;

CREATE POLICY "Admins can manage all taxonomy entries"
ON public.document_taxonomy
FOR ALL
TO authenticated
USING (public.is_admin())
WITH CHECK (public.is_admin());

-- 2. Performance: Ensure Index on document_id exists (critical for ON DELETE CASCADE)
CREATE INDEX IF NOT EXISTS idx_document_taxonomy_document_id ON public.document_taxonomy(document_id);

COMMIT;
