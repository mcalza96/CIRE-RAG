BEGIN;

-- Helper function for safe casting
CREATE OR REPLACE FUNCTION public.safe_cast_uuid(text) RETURNS uuid AS $$
BEGIN
  RETURN $1::uuid;
EXCEPTION WHEN OTHERS THEN
  RETURN NULL;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Drop failing policies
drop policy if exists "Course Scope Isolation: Upload" on storage.objects;
drop policy if exists "Course Scope Isolation: Select" on storage.objects;
drop policy if exists "Course Scope Isolation: Delete" on storage.objects;

-- Re-create with Check AND Safe Cast
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

COMMIT;
