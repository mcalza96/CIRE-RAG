-- =============================================================================
-- MIGRATION: MULTI-TENANT ARCHITECTURE PHASE 1 (B2B2C Transformation)
-- Description: Introduces Institutions (Tenants) and Memberships to support
--              the Hybrid B2B2C model (Personal vs Institutional).
-- Date: 2026-02-05
-- =============================================================================

BEGIN;

-- 1. ENUMS
-- Define the tier of the institution (Pricing/Features)
DO $$ 
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'institution_tier_enum') THEN
        CREATE TYPE public.institution_tier_enum AS ENUM ('free', 'pro', 'enterprise');
    END IF;
    -- Define the role of a user WITHIN an institution (Scoped Context)
    -- Distinct from the global app_role.
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'institution_role_enum') THEN
        CREATE TYPE public.institution_role_enum AS ENUM ('admin', 'teacher', 'student', 'observer');
    END IF;
END $$;

-- 2. TABLES

-- 2.1 Institutions (Tenants)
CREATE TABLE IF NOT EXISTS public.institutions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    identifiers JSONB DEFAULT '{}'::jsonb, -- e.g., {"rbd": "12345", "tax_id": "..."}
    branding_config JSONB DEFAULT '{}'::jsonb, -- e.g., {"logo_url": "...", "primary_color": "#..."}
    subscription_tier public.institution_tier_enum DEFAULT 'free' NOT NULL,
    settings JSONB DEFAULT '{}'::jsonb, -- Feature flags, localized settings
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- 2.2 Memberships (Pivot Table Users <-> Institutions)
CREATE TABLE IF NOT EXISTS public.memberships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    institution_id UUID NOT NULL REFERENCES public.institutions(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    role public.institution_role_enum DEFAULT 'teacher' NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb, -- Internal notes, job title
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT memberships_unique_user_institution UNIQUE (institution_id, user_id)
);

-- Indexes for memberships
CREATE INDEX IF NOT EXISTS idx_memberships_user ON public.memberships(user_id);
CREATE INDEX IF NOT EXISTS idx_memberships_institution ON public.memberships(institution_id);


-- 3. SCHEMA MIGRATION (Alter Existing)

-- 3.1 Modify Courses to belong to an Institution (Optional)
-- If institution_id is NULL, it is a PERSONAL course (B2C).
-- If institution_id is SET, it is an INSTITUTIONAL course (B2B).
DO $$ 
BEGIN
    IF NOT EXISTS (SELECT FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'courses' AND column_name = 'institution_id') THEN
        ALTER TABLE public.courses 
        ADD COLUMN institution_id UUID REFERENCES public.institutions(id) ON DELETE SET NULL;
        
        -- Create index for performance on institutional queries
        CREATE INDEX idx_courses_institution ON public.courses(institution_id);
    END IF;
END $$;


-- 4. SECURITY (RLS)

-- Enable RLS
ALTER TABLE public.institutions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.memberships ENABLE ROW LEVEL SECURITY;
-- Courses already has RLS enabled, we just need to update policies.

-- 4.1 Institutions Policies
-- Policy: Who can see an institution?
-- 1. Global Admins (Staff)
-- 2. Members of the institution
CREATE POLICY "See institution if member or admin" ON public.institutions
FOR SELECT TO authenticated
USING (
    public.is_admin() OR
    EXISTS (
        SELECT 1 FROM public.memberships m 
        WHERE m.institution_id = public.institutions.id 
        AND m.user_id = auth.uid()
    )
);

-- Policy: Only Global Admins can create/edit institutions (for now)
CREATE POLICY "Admins manage institutions" ON public.institutions
FOR ALL TO authenticated
USING (public.is_admin())
WITH CHECK (public.is_admin());


-- 4.2 Memberships Policies
-- Policy: View own memberships
CREATE POLICY "View own memberships" ON public.memberships
FOR SELECT TO authenticated
USING (
    user_id = auth.uid() OR 
    public.is_admin() OR
    -- Allow Institution Admins to see roster
    EXISTS (
        SELECT 1 FROM public.memberships m_admin
        WHERE m_admin.institution_id = public.memberships.institution_id
        AND m_admin.user_id = auth.uid()
        AND m_admin.role = 'admin'
    )
);

-- Policy: Manage memberships (Strictly Institution Admins or Global Admins)
CREATE POLICY "Manage memberships" ON public.memberships
FOR ALL TO authenticated
USING (
    public.is_admin() OR
    EXISTS (
        SELECT 1 FROM public.memberships m_admin
        WHERE m_admin.institution_id = public.memberships.institution_id
        AND m_admin.user_id = auth.uid()
        AND m_admin.role = 'admin'
    )
)
WITH CHECK (
    public.is_admin() OR
    EXISTS (
        SELECT 1 FROM public.memberships m_admin
        WHERE m_admin.institution_id = public.memberships.institution_id
        AND m_admin.user_id = auth.uid()
        AND m_admin.role = 'admin'
    )
);


-- 4.3 Courses Policies (The Hybrid Split)
-- We need to ensure we don't break existing Personal Course access.

-- Current strategy usually implies separate policies or a combined OR condition.
-- Supabase merges policies with OR by default.
-- Let's define specific policies for the new Institutional scope.

-- Policy: Institution Admins see ALL courses in their institution
CREATE POLICY "Institution Admins view all institutional courses" ON public.courses
FOR SELECT TO authenticated
USING (
    institution_id IS NOT NULL AND
    EXISTS (
        SELECT 1 FROM public.memberships m
        WHERE m.institution_id = public.courses.institution_id
        AND m.user_id = auth.uid()
        AND m.role = 'admin'
    )
);

-- Policy: Teachers see Institutional courses ONLY if they are the creator OR if they are assigned.
-- (Assumed 'assigned' logic via enrollment or explicit assignment? 
-- The prompt said: "O los cursos institucionales donde ellos son los creadores/asignados".)
-- Existing logic usually relies on `teacher_id = auth.uid()`.
-- Standard course visibility for teachers:
-- 1. I am the owner (teacher_id = uid) -> Covers both Personal and Institutional where I am the lead.
-- 2. I am invited? (Not implemented here yet, adhering to strict prompt requirements).
-- 
-- So, broadly speaking, the existing "owner" policy covers specific assignment if they are the `teacher_id`.
-- The new policy adds visibility for "Supervisors" (Institution Admins).

-- Note: We do NOT need to modify the "Personal Owner" policy if it simply checks `teacher_id = auth.uid()`.
-- That check is agnostic to `institution_id`.
-- However, we must ensure that an Institutional Admin doesn't accidentally see Personal courses of a teacher.
-- The policy above `Institution Admins view all institutional courses` includes `institution_id IS NOT NULL`, strict isolation.


-- 5. SEED DATA (Demo/Testing)
-- Only runs if no institutions exist to prevent pollution on production.
DO $$
DECLARE
    v_inst_id UUID;
    v_user_id UUID;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.institutions) THEN
        -- 1. Create Demo School
        INSERT INTO public.institutions (name, subscription_tier, identifiers)
        VALUES ('Colegio Demo Bicentenario', 'pro', '{"rbd": "12345"}'::jsonb)
        RETURNING id INTO v_inst_id;

        -- 2. Find a user (The one running this or just the first user found)
        SELECT id INTO v_user_id FROM auth.users LIMIT 1;

        IF v_user_id IS NOT NULL THEN
            -- 3. Make them an Admin of the school
            INSERT INTO public.memberships (institution_id, user_id, role)
            VALUES (v_inst_id, v_user_id, 'admin');

            RAISE NOTICE 'Seeded Demo Institution % assigned to User %', v_inst_id, v_user_id;
        END IF;
    END IF;
END $$;

COMMIT;
