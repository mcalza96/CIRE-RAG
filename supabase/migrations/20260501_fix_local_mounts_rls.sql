-- MIGRATION: Fix RLS for Course Content Mounts (Local Mounts)
-- Description: Explicitly allows teachers to INSERT and DELETE their own mounts.
-- Date: 2026-05-01

BEGIN;

-- Drop existing overlapping policies if any (usually 'Users can view...' is read-only)
DROP POLICY IF EXISTS "Teachers manage mounts for their courses" ON public.course_content_mounts;

-- Create comprehensive policy for ALL operations (SELECT, INSERT, UPDATE, DELETE)
CREATE POLICY "Teachers manage mounts for their courses" ON public.course_content_mounts
    FOR ALL
    TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.courses
            WHERE courses.id = course_content_mounts.course_id
            AND courses.teacher_id = auth.uid()
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM public.courses
            WHERE courses.id = course_content_mounts.course_id
            AND courses.teacher_id = auth.uid()
        )
    );

COMMIT;
