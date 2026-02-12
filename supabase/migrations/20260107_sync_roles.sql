DO $$
DECLARE
    user_record record;
    profile_record record;
BEGIN
    -- Iterate over all users in auth.users
    FOR user_record IN SELECT id, raw_app_meta_data FROM auth.users LOOP
        -- Check if a corresponding profile exists
        SELECT role INTO profile_record FROM public.profiles WHERE id = user_record.id;
        
        IF FOUND THEN
            -- Update raw_app_meta_data with the role from profiles
            -- We preserve existing metadata and only update/insert the 'role' key
            UPDATE auth.users
            SET raw_app_meta_data = 
                COALESCE(user_record.raw_app_meta_data, '{}'::jsonb) || 
                jsonb_build_object('role', profile_record.role)
            WHERE id = user_record.id;
            
            RAISE NOTICE 'Updated role for user %: %', user_record.id, profile_record.role;
        ELSE
            RAISE NOTICE 'No profile found for user %', user_record.id;
        END IF;
    END LOOP;
END $$;
