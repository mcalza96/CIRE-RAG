-- =============================================================================
-- MIGRATION: FIX CONTENT ISOLATION (Hard Shell)
-- Description: Enforces strict data isolation between Global and Institutional content
--              using RLS based on `institution_id`.
-- Date: 2026-02-04
-- =============================================================================

BEGIN;

-- 1. Ensure `institution_id` exists in source_documents (just in case)
-- Based on analysis, it likely exists or is handled by `meta` but previous migration `20260205_multi_tenant_phase1.sql` 
-- added it to `courses`. `SupabaseInstitutionalContentRepository` uses it in insert.
-- Let's ensure the column exists on `source_documents` to be safe/explicit.

DO $$ 
BEGIN
    IF NOT EXISTS (SELECT FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'source_documents' AND column_name = 'institution_id') THEN
        ALTER TABLE public.source_documents 
        ADD COLUMN institution_id UUID REFERENCES public.institutions(id) ON DELETE CASCADE;
        
        CREATE INDEX idx_source_documents_institution ON public.source_documents(institution_id);
    END IF;
    
    -- Also ensure `is_global` exists if strictly needed, though `institution_id IS NULL` is the source of truth.
    -- The repository inserts `is_global: false`.
    IF NOT EXISTS (SELECT FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'source_documents' AND column_name = 'is_global') THEN
        ALTER TABLE public.source_documents 
        ADD COLUMN is_global BOOLEAN DEFAULT false;
    END IF;
END $$;

-- 2. ENABLE RLS (Idempotent)
ALTER TABLE public.source_documents ENABLE ROW LEVEL SECURITY;

-- 3. DROP EXISTING WEAK POLICIES (If any) to restart fresh
DROP POLICY IF EXISTS "Course owners can manage source_documents" ON public.source_documents;
DROP POLICY IF EXISTS "Global Admins see Global Content" ON public.source_documents;
DROP POLICY IF EXISTS "Tenant Admins see Tenant Content" ON public.source_documents;

-- 4. CREATE STRICT POLICIES

-- Policy 1: Global Content Visibility
-- "Super Admins (Global) can see content where institution_id IS NULL"
-- Also allows service_role to bypass.
CREATE POLICY "Global Admins see Global Content"
ON public.source_documents
FOR ALL
TO authenticated, service_role
USING (
    -- Condition: User is Super Admin AND Content is Global
    (
        public.is_admin() -- Checks app_metadata -> app_role = 'super-admin'
        AND 
        institution_id IS NULL
    ) 
    OR 
    -- Allow service_role to see everything (optional, but good for backend jobs)
    (auth.jwt() ->> 'role' = 'service_role')
);

-- Policy 2: Institutional Content Visibility
-- "Tenant Admins can see content where institution_id matches their permitted context"
-- Note: 'is_admin()' usually checks global admin. 
-- For Tenant Admin, we check memberships.
CREATE POLICY "Tenant Admins see Tenant Content"
ON public.source_documents
FOR ALL
TO authenticated
USING (
    institution_id IS NOT NULL 
    AND
    EXISTS (
        SELECT 1 FROM public.memberships m
        WHERE m.institution_id = public.source_documents.institution_id
        AND m.user_id = auth.uid()
        AND m.role = 'admin' -- Only Institutional Admins can manage knowledge for now
    )
)
WITH CHECK (
    institution_id IS NOT NULL 
    AND
    EXISTS (
        SELECT 1 FROM public.memberships m
        WHERE m.institution_id = public.source_documents.institution_id
        AND m.user_id = auth.uid()
        AND m.role = 'admin'
    )
);

-- Policy 3: Teachers/Students? 
-- The prompt focused on Admin isolation. 
-- "Tenant Admin (Institution View): SOLO vea documentos..."
-- We can add read-only policies for teachers later if needed, but for now we secure the "Admin Knowledge Base".

COMMIT;
