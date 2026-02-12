-- ========================================================
-- TEACHEROS - MIGRACIÓN: AUTO ENROLLMENT TRIGGER (FASE 4)
-- ========================================================
-- Descripción: Automatiza la creación de registros de estudiantes (learners)
-- y su vinculación a cohortes cuando un usuario se registra con un email
-- que tiene invitaciones pendientes.
-- ========================================================

BEGIN;

-- 1. FUNCIÓN TRIGGER: handle_new_student_onboarding
-- --------------------------------------------------------
CREATE OR REPLACE FUNCTION public.handle_new_student_onboarding()
RETURNS TRIGGER
SECURITY DEFINER
SET search_path = public
LANGUAGE plpgsql
AS $$
DECLARE
    invitation_record RECORD;
    learner_exists BOOLEAN;
BEGIN
    -- [A] Buscar invitaciones pendientes para este email
    -- Se usa un loop por si el estudiante fue invitado a múltiples cursos simultáneamente
    FOR invitation_record IN 
        SELECT id, cohort_id 
        FROM public.cohort_invitations 
        WHERE email = NEW.email 
        AND status = 'pending'
    LOOP
        -- [B] Auto-Provisioning de Identidad Pedagógica (Learners)
        -- Si el learner no existe, lo creamos forzando que su ID coincida con el Auth ID (New.id)
        -- Esto sincroniza la identidad de autenticación con la identidad de aprendizaje.
        BEGIN
            INSERT INTO public.learners (id, display_name, avatar_url)
            VALUES (
                NEW.id, 
                COALESCE(NEW.raw_user_meta_data->>'full_name', NEW.email),
                COALESCE(NEW.raw_user_meta_data->>'avatar_url', '')
            )
            ON CONFLICT (id) DO NOTHING;
        EXCEPTION WHEN OTHERS THEN
            -- Logging de error no bloqueante
            RAISE WARNING 'Error auto-provisioning learner for user %: %', NEW.id, SQLERRM;
        END;

        -- [C] Auto-Enrollment (Vincular al Cohorte)
        BEGIN
            INSERT INTO public.cohort_members (cohort_id, student_id, status, joined_at)
            VALUES (
                invitation_record.cohort_id, 
                NEW.id, 
                'active', 
                NOW()
            )
            ON CONFLICT (cohort_id, student_id) DO NOTHING;
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING 'Error enrolling user % to cohort %: %', NEW.id, invitation_record.cohort_id, SQLERRM;
        END;

        -- [D] Cerrar la Invitación
        UPDATE public.cohort_invitations 
        SET status = 'accepted' 
        WHERE id = invitation_record.id;
        
    END LOOP;

    RETURN NEW;
END;
$$;

-- 2. CREACIÓN DEL TRIGGER
-- --------------------------------------------------------
-- Se ejecuta DESPUÉS de que el usuario se ha verificado/creado en Auth.
DROP TRIGGER IF EXISTS on_student_signup_enrollment ON auth.users;

CREATE TRIGGER on_student_signup_enrollment
AFTER INSERT ON auth.users
FOR EACH ROW
EXECUTE FUNCTION public.handle_new_student_onboarding();

COMMIT;
