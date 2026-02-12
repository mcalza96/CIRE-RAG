BEGIN;

-- 1. Helper Function: Safe UUID Casting
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.safe_cast_uuid(text) RETURNS uuid AS $$
BEGIN
  RETURN $1::uuid;
EXCEPTION WHEN OTHERS THEN
  RETURN NULL;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- 2. Clean Slate: Drop ALL known policies on storage.objects
-- ----------------------------------------------------------------------------
-- From RAG Ingestion
DROP POLICY IF EXISTS "Course Scope Isolation: Upload" ON storage.objects;
DROP POLICY IF EXISTS "Course Scope Isolation: Select" ON storage.objects;
DROP POLICY IF EXISTS "Course Scope Isolation: Delete" ON storage.objects;
DROP POLICY IF EXISTS "Professors can insert documents for their courses" ON storage.objects; -- Just in case

-- From Canvas Lite
DROP POLICY IF EXISTS "course_materials_staff_all" ON storage.objects;
DROP POLICY IF EXISTS "course_materials_student_read" ON storage.objects;

-- 3. Re-Create RAG POLICIES (Bucket: course_knowledge)
-- ----------------------------------------------------------------------------
create policy "Course Scope Isolation: Upload"
on storage.objects for insert
to authenticated
with check (
    bucket_id = 'course_knowledge' AND
    (
        -- CASE 1: Global Content (Admin Only)
        (
            (storage.foldername(name))[1] = 'global' 
            AND public.is_admin()
        )
        OR
        -- CASE 2: Course Content (Teacher Ownership)
        (
            (storage.foldername(name))[1] != 'global'
            AND (
                exists (
                    select 1 from public.courses c
                    where c.id = public.safe_cast_uuid((storage.foldername(name))[1])
                    and c.teacher_id = auth.uid()
                )
                OR public.is_admin()
            )
        )
    )
);

create policy "Course Scope Isolation: Select"
on storage.objects for select
to authenticated
using (
    bucket_id = 'course_knowledge' AND
    (
        -- CASE 1: Global Content (Admin Only)
        (
            (storage.foldername(name))[1] = 'global' 
            AND public.is_admin()
        )
        OR
        -- CASE 2: Course Content (Teacher Ownership)
        (
            (storage.foldername(name))[1] != 'global'
            AND (
                exists (
                    select 1 from public.courses c
                    where c.id = public.safe_cast_uuid((storage.foldername(name))[1])
                    and c.teacher_id = auth.uid()
                )
                OR public.is_admin()
            )
        )
    )
);

create policy "Course Scope Isolation: Delete"
on storage.objects for delete
to authenticated
using (
    bucket_id = 'course_knowledge' AND
    (
        -- CASE 1: Global Content (Admin Only)
        (
            (storage.foldername(name))[1] = 'global' 
            AND public.is_admin()
        )
        OR
        -- CASE 2: Course Content (Teacher Ownership)
        (
            (storage.foldername(name))[1] != 'global'
            AND (
                exists (
                    select 1 from public.courses c
                    where c.id = public.safe_cast_uuid((storage.foldername(name))[1])
                    and c.teacher_id = auth.uid()
                )
                OR public.is_admin()
            )
        )
    )
);

-- 4. Re-Create CANVAS LITE POLICIES (Bucket: course-materials)
-- ----------------------------------------------------------------------------
-- Política 1: Acceso completo para Staff (Gestión de Materiales)
CREATE POLICY "course_materials_staff_all" ON storage.objects
FOR ALL TO authenticated
USING (
    bucket_id = 'course-materials' AND 
    public.is_staff()
)
WITH CHECK (
    bucket_id = 'course-materials' AND 
    public.is_staff()
);

-- Política 2: Lectura para Estudiantes asociados al curso de la lección
-- Fix: Use safe_cast or string matching to avoid UUID crashes if path structure changes
CREATE POLICY "course_materials_student_read" ON storage.objects
FOR SELECT TO authenticated
USING (
    bucket_id = 'course-materials' AND (
        public.is_staff() OR
        EXISTS (
            SELECT 1 FROM public.lessons l
            JOIN public.courses c ON c.id = l.course_id
            JOIN public.teacher_student_mapping tsm ON tsm.teacher_id = c.teacher_id
            WHERE tsm.student_id = auth.uid()
            -- We just check string containment for safety instead of complex UUID parsing for now
            AND storage.objects.name LIKE 'lessons/' || l.id || '/%'
        )
    )
);

COMMIT;
