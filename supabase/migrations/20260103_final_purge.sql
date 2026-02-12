-- 1. Migrate Roles (Safely add value and update)
DO $$
BEGIN
    -- Ensure 'teacher' value exists in app_role enum
    IF NOT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON e.enumtypid = t.oid WHERE t.typname = 'app_role' AND e.enumlabel = 'teacher') THEN
        ALTER TYPE public.app_role ADD VALUE 'teacher';
    END IF;

    -- RENOMBRADO DE COLUMNAS (Operación Atómica)
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'learners' AND column_name = 'parent_id') THEN
        ALTER TABLE public.learners RENAME COLUMN parent_id TO teacher_id;
    END IF;
    
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'feedback_messages' AND column_name = 'parent_id') THEN
        ALTER TABLE public.feedback_messages RENAME COLUMN parent_id TO teacher_id;
    END IF;
END $$;

-- 2. Update Roles and Defaults
UPDATE public.profiles SET role = 'teacher' WHERE role::text IN ('family', 'user');
ALTER TABLE public.profiles ALTER COLUMN role SET DEFAULT 'teacher';

-- 3. Update RLS Policies (Separated to ensure schema visibility)
DO $$
BEGIN
    -- Verificar existencia de teacher_id antes de crear políticas
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'learners' AND column_name = 'teacher_id') THEN
        
        EXECUTE 'DROP POLICY IF EXISTS "Los padres pueden ver sus propios alumnos" ON public.learners';
        EXECUTE 'DROP POLICY IF EXISTS "Los padres pueden gestionar sus propios alumnos" ON public.learners';
        EXECUTE 'DROP POLICY IF EXISTS "Professors can manage their own students" ON public.learners';
        
        EXECUTE 'CREATE POLICY "Professors can manage their own students"
                 ON public.learners FOR ALL TO authenticated
                 USING (teacher_id = auth.uid() OR public.is_admin())
                 WITH CHECK (teacher_id = auth.uid() OR public.is_admin())';

        -- Update RLS Policies for Learner Progress (linked to learners.teacher_id)
        EXECUTE 'DROP POLICY IF EXISTS "Gestion de progreso" ON public.learner_progress';
        EXECUTE 'CREATE POLICY "Gestion de progreso" 
                 ON public.learner_progress FOR ALL 
                 USING (learner_id IN (SELECT id FROM public.learners WHERE teacher_id = auth.uid() OR public.is_admin()))';
    ELSE
        RAISE WARNING 'No se pudo crear la política de RLS: La columna teacher_id no existe en la tabla learners.';
    END IF;
END $$;

-- 4. Update Trigger Function
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO public.profiles (id, email, full_name, avatar_url, role)
  VALUES (
    new.id, 
    new.email, 
    new.raw_user_meta_data->>'full_name', 
    new.raw_user_meta_data->>'avatar_url',
    COALESCE(new.raw_user_meta_data->>'role', 'teacher')::public.app_role
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
