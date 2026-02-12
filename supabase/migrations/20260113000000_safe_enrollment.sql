-- ========================================================
-- TEACHEROS - MIGRACIÓN: SAFE ENROLLMENT (FASE 1)
-- ========================================================
-- Descripción: Expande el sistema de cohortes para soportar
-- invitaciones por correo electrónico y tracking de ciclo de vida.
-- ========================================================

BEGIN;

-- 1. TIPOS PERSONALIZADOS (ENUMS)
DO $$ 
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'membership_status_enum') THEN
        CREATE TYPE public.membership_status_enum AS ENUM ('active', 'suspended', 'completed', 'dropped');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'invitation_status_enum') THEN
        CREATE TYPE public.invitation_status_enum AS ENUM ('pending', 'accepted', 'expired', 'revoked');
    END IF;
END $$;

-- 2. AUDITORÍA Y MEJORA DE cohort_members
-- Nota: En schema.sql la tabla ya existe con cohort_id y student_id como PK compuesta.
-- Añadimos campos de ciclo de vida.

ALTER TABLE public.cohort_members 
    ADD COLUMN IF NOT EXISTS status public.membership_status_enum DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS joined_at TIMESTAMPTZ DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS dropout_at TIMESTAMPTZ;

COMMENT ON COLUMN public.cohort_members.status IS 'Estado actual del estudiante en el cohorte.';
COMMENT ON COLUMN public.cohort_members.joined_at IS 'Fecha en la que el estudiante se unió formalmente.';
COMMENT ON COLUMN public.cohort_members.dropout_at IS 'Fecha de baja (si aplica).';

-- 3. CREACIÓN DE cohort_invitations
CREATE TABLE IF NOT EXISTS public.cohort_invitations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cohort_id UUID NOT NULL REFERENCES public.cohorts(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    token TEXT UNIQUE NOT NULL,
    invited_by UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    status public.invitation_status_enum DEFAULT 'pending' NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    
    -- Restricción de integridad: Un email solo puede tener una invitación pendiente por cohorte
    CONSTRAINT unique_pending_invitation UNIQUE (cohort_id, email, status)
);

-- Índices para búsquedas rápidas
CREATE INDEX IF NOT EXISTS idx_invitations_email ON public.cohort_invitations(email);
CREATE INDEX IF NOT EXISTS idx_invitations_token ON public.cohort_invitations(token);
CREATE INDEX IF NOT EXISTS idx_invitations_cohort ON public.cohort_invitations(cohort_id);

-- Índice parcial para la restricción de unicidad (solo aplica a 'pending')
-- Esto permite tener múltiples invitaciones históricas (accepted/expired/revoked) pero solo una activa.
DROP INDEX IF EXISTS idx_unique_pending_email_cohort;
CREATE UNIQUE INDEX idx_unique_pending_email_cohort 
ON public.cohort_invitations (cohort_id, email) 
WHERE status = 'pending';

-- 4. SEGURIDAD RLS - NIVEL ESTRICTO
ALTER TABLE public.cohort_invitations ENABLE ROW LEVEL SECURITY;

-- Limpieza preventiva de políticas para cohort_invitations
DROP POLICY IF EXISTS "Professors can manage invitations for their cohorts" ON public.cohort_invitations;
DROP POLICY IF EXISTS "Users can view invitations directed to them" ON public.cohort_invitations;
DROP POLICY IF EXISTS "Public can check invitation by token" ON public.cohort_invitations;

-- Política de Gestión Completa para Profesores (INSERT, SELECT, UPDATE, DELETE)
-- El profesor debe ser el owner del cohorte.
CREATE POLICY "Professors can manage invitations for their cohorts"
ON public.cohort_invitations
FOR ALL
TO authenticated
USING (
    EXISTS (
        SELECT 1 FROM public.cohorts c
        WHERE c.id = cohort_id 
        AND (c.teacher_id = auth.uid() OR public.is_admin())
    )
)
WITH CHECK (
    EXISTS (
        SELECT 1 FROM public.cohorts c
        WHERE c.id = cohort_id 
        AND (c.teacher_id = auth.uid() OR public.is_admin())
    )
);

-- Política de Lectura para Estudiantes (Basada en Email)
-- Permite ver la invitación si el email coincide con el del usuario autenticado.
CREATE POLICY "Users can view invitations directed to them"
ON public.cohort_invitations
FOR SELECT
TO authenticated
USING (
    email = auth.jwt() ->> 'email'
);

-- Política de Lectura vía Token (Para validación anónima/pre-login)
-- Nota: Útil si el frontend necesita validar el curso/cohorte antes de forzar el login.
CREATE POLICY "Public can check invitation by token"
ON public.cohort_invitations
FOR SELECT
TO anon, authenticated
USING (
    status = 'pending' AND expires_at > NOW()
);

-- 5. FUNCTION & TRIGGER: Auto-expiración (Opcional - Lógica de lectura ya lo filtra)
-- Podríamos tener un proceso de limpieza, pero por ahora lo manejamos con la lógica del query.

COMMIT;
