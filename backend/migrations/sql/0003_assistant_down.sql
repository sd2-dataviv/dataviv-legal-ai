DROP TABLE IF EXISTS thread_messages CASCADE;
DROP TABLE IF EXISTS threads CASCADE;
DO $$
BEGIN
    IF to_regclass('public.firms') IS NOT NULL THEN
        REVOKE UPDATE (model_policy) ON firms FROM app_user;
    END IF;
END $$;
