-- Step 2f: the shared API on WorkflowHub. A call from another app or an AI agent names a person by e-mail and
-- has no firm context, so RLS hides every user. This SECURITY DEFINER lookup returns only id, firm, role and
-- e-mail of ACTIVE users with that address (at most two, enough to detect an ambiguous caller); the request
-- itself then runs in an ordinary firm-scoped transaction as that user (the pattern of esign_request_firm).
CREATE OR REPLACE FUNCTION platform_user_by_email(p_email text)
RETURNS TABLE (user_id uuid, firm_id uuid, role text, email text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT u.id, u.firm_id, u.role::text, u.email FROM users u
     WHERE lower(u.email) = lower(btrim(p_email)) AND u.is_active
     ORDER BY u.created_at
     LIMIT 2
$$;

REVOKE ALL ON FUNCTION platform_user_by_email(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform_user_by_email(text) TO app_user;
