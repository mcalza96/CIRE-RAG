-- Migration to sync user role from public.profiles to auth.users.app_metadata
-- Allows RBAC in Edge functions/middleware without extra DB calls

-- 1. Create the sync function (SECURITY DEFINER to access auth schema)
CREATE OR REPLACE FUNCTION public.handle_sync_user_role()
RETURNS TRIGGER AS $$
BEGIN
  -- Update auth.users.raw_app_meta_data with the new role
  -- We preserve existing metadata and only update/insert the 'role' key
  UPDATE auth.users
  SET raw_app_meta_data = 
    COALESCE(raw_app_meta_data, '{}'::jsonb) || 
    jsonb_build_object('role', NEW.role)
  WHERE id = NEW.id;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 2. Create the trigger on public.profiles
DROP TRIGGER IF EXISTS on_profile_role_change ON public.profiles;

CREATE TRIGGER on_profile_role_change
AFTER INSERT OR UPDATE OF role ON public.profiles
FOR EACH ROW
EXECUTE FUNCTION public.handle_sync_user_role();

-- 3. (Optional) Backfill existing users (Heavy operation, run manually if needed)
-- UPDATE auth.users u
-- SET raw_app_meta_data = 
--   COALESCE(u.raw_app_meta_data, '{}'::jsonb) || 
--   jsonb_build_object('role', p.role)
-- FROM public.profiles p
-- WHERE u.id = p.id AND (u.raw_app_meta_data->>'role' IS DISTINCT FROM p.role);

