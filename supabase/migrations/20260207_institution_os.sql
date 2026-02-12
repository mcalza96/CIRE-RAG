-- =============================================================================
-- MIGRATION: INSTITUTION OS BACKBONE
-- Description: Adds 'invitations' table for managing B2B roster growth.
-- Date: 2026-02-07
-- =============================================================================

BEGIN;

-- 1. TABLE: Invitations
-- Stores pending invites sent by Institution Admins.
CREATE TABLE IF NOT EXISTS public.invitations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    institution_id UUID NOT NULL REFERENCES public.institutions(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    role public.institution_role_enum DEFAULT 'teacher' NOT NULL,
    token TEXT NOT NULL DEFAULT encode(gen_random_bytes(32), 'hex'), -- Simple token for magic link
    status TEXT NOT NULL CHECK (status IN ('pending', 'accepted', 'expired')) DEFAULT 'pending',
    invited_by UUID REFERENCES public.profiles(id), -- Audit who sent it
    created_at TIMESTAMPTZ DEFAULT now(),
    expires_at TIMESTAMPTZ DEFAULT (now() + interval '7 days'),
    
    -- Constraint: Avoid spamming same email for same institution while pending
    CONSTRAINT uq_invitations_active UNIQUE (institution_id, email)
);

-- Index for lookup by token (Magic Link)
CREATE INDEX IF NOT EXISTS idx_invitations_token ON public.invitations(token);
CREATE INDEX IF NOT EXISTS idx_invitations_institution ON public.invitations(institution_id);

-- 2. SECURITY (RLS)
ALTER TABLE public.invitations ENABLE ROW LEVEL SECURITY;

-- Policy: Admins can View/Manage invitations for THEIR institution
CREATE POLICY "Admins manage institution invitations"
ON public.invitations
FOR ALL
TO authenticated
USING (
    EXISTS (
        SELECT 1 FROM public.memberships m
        WHERE m.institution_id = public.invitations.institution_id
        AND m.user_id = auth.uid()
        AND m.role = 'admin'
    )
)
WITH CHECK (
    EXISTS (
        SELECT 1 FROM public.memberships m
        WHERE m.institution_id = public.invitations.institution_id
        AND m.user_id = auth.uid()
        AND m.role = 'admin'
    )
);

-- Policy: Anonym/Public access via Token? 
-- Usually accepting an invite happens via a Server Action or API that bypasses RLS with service_role,
-- or strictly authenticated if the user must register first.
-- Let's keep RLS strict for now (Admins only). The server action will handle the acceptance logic using admin context.

COMMIT;
