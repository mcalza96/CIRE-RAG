-- Migration: Teacher Role Autonomy (Granular RLS for Exams)
-- Description: Refactors exams table policies to enable teacher autonomy with strict tenant isolation
-- Date: 2026-01-02
-- Author: CISRE Security Team

-- =============================================================================
-- OBJECTIVE: Enable autonomous exam management for teachers while maintaining:
-- 1. Tenant Isolation (teachers only manage their own exams)
-- 2. Forensic Immutability (PUBLISHED exams cannot be modified)
-- 3. Forensic Access (teachers can audit only their own exams' attempts)
-- =============================================================================

-- Note: is_staff() function already includes 'teacher' role (verified in hardening_policies.sql)
-- Note: exam_attempts and telemetry_logs policies already properly scoped (no changes needed)

-- -----------------------------------------------------------------------------
-- REFACTOR: exams Table RLS Policies
-- Replace broad "FOR ALL" policy with granular INSERT, UPDATE, DELETE, SELECT
-- -----------------------------------------------------------------------------

-- 1. Drop the old broad policy
DROP POLICY IF EXISTS "Professors can manage their own exams" ON public.exams;

-- 2. CREATE POLICY: Teachers can create exams (INSERT)
-- Business Rule: creator_id must match auth.uid() to ensure tenant isolation
CREATE POLICY "Teachers can create exams"
ON public.exams FOR INSERT 
TO authenticated
WITH CHECK (
    creator_id = auth.uid() AND
    public.is_staff()
);

-- 3. CREATE POLICY: Teachers can view their own exams (SELECT)
-- Business Rule: Teachers see only exams they created OR admins see all
CREATE POLICY "Teachers can view their own exams"
ON public.exams FOR SELECT
TO authenticated
USING (
    creator_id = auth.uid() OR 
    public.is_admin()
);

-- 4. CREATE POLICY: Teachers can update their own DRAFT exams (UPDATE)
-- Business Rule: Can only update own exams AND only if status != 'PUBLISHED'
-- Note: The trigger trg_exam_inmutability provides additional immutability protection
CREATE POLICY "Teachers can update their own draft exams"
ON public.exams FOR UPDATE
TO authenticated
USING (
    creator_id = auth.uid() AND
    status != 'PUBLISHED'
)
WITH CHECK (
    creator_id = auth.uid() AND
    status != 'PUBLISHED'
);

-- 5. CREATE POLICY: Teachers can delete their own DRAFT exams (DELETE)
-- Business Rule: Can only delete own exams AND only if status != 'PUBLISHED'
CREATE POLICY "Teachers can delete their own draft exams"
ON public.exams FOR DELETE
TO authenticated
USING (
    creator_id = auth.uid() AND
    status != 'PUBLISHED'
);

-- -----------------------------------------------------------------------------
-- VERIFICATION NOTES
-- -----------------------------------------------------------------------------
-- The following security guarantees are now enforced:
--
-- ✅ Tenant Isolation:
--    - Teachers can ONLY create exams with their own creator_id
--    - Teachers can ONLY view/update/delete their own exams
--
-- ✅ Forensic Immutability:
--    - PUBLISHED exams cannot be updated (blocked by policy + trigger)
--    - PUBLISHED exams cannot be deleted (blocked by policy)
--
-- ✅ Forensic Access:
--    - Teachers can view exam_attempts for their own exams (existing policy)
--    - Teachers can view telemetry_logs for their own exams (existing policy)
--
-- ✅ Admin Override:
--    - Admins can view all exams (SELECT policy includes is_admin())
--    - Other admin operations handled by existing admin bypass logic
-- -----------------------------------------------------------------------------

-- Migration complete. Teachers now have autonomous exam management while
-- maintaining strict security boundaries and forensic data integrity.
