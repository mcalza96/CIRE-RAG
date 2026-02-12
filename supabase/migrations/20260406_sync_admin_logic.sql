BEGIN;

-- Sync public.is_admin() with Application Logic (Hardcoded Super Admins)
-- This ensures that the RLS policies respect the same admins as the Next.js middleware/actions.

CREATE OR REPLACE FUNCTION public.is_admin()
RETURNS BOOLEAN AS $$
BEGIN
  -- Check 1: Role-based (Existing)
  IF (SELECT (role = 'admin') FROM public.profiles WHERE id = auth.uid()) THEN
    RETURN TRUE;
  END IF;

  -- Check 2: Email-based (Super Admin Override)
  -- Uses Supabase auth.jwt() -> email claim for security check in SQL
  RETURN (
    SELECT auth.jwt() ->> 'email' IN (
        'marcelo.calzadilla@jitdata.cl',
        'admin@procreatealpha.studio'
    )
  );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

COMMIT;
