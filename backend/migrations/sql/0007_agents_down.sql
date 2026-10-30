DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'public' AND table_name = 'approvals' AND column_name = 'agent_run_id') THEN
        DELETE FROM approvals WHERE agent_run_id IS NOT NULL;
        DROP INDEX IF EXISTS ux_approvals_one_pending_per_agent_step;
        ALTER TABLE approvals DROP CONSTRAINT IF EXISTS approvals_one_subject;
        ALTER TABLE approvals DROP CONSTRAINT IF EXISTS approvals_required_role_check;
        UPDATE approvals SET required_role = 'matter_manager' WHERE required_role = 'matter_editor';
        ALTER TABLE approvals ADD CONSTRAINT approvals_required_role_check
            CHECK (required_role IN ('matter_manager', 'firm_partner'));
        ALTER TABLE approvals DROP COLUMN IF EXISTS detail;
        ALTER TABLE approvals DROP COLUMN IF EXISTS agent_step_id;
        ALTER TABLE approvals DROP COLUMN IF EXISTS agent_run_id;
        ALTER TABLE approvals ALTER COLUMN run_id SET NOT NULL;
        ALTER TABLE approvals ALTER COLUMN step_id SET NOT NULL;
    END IF;
END $$;
DROP TABLE IF EXISTS matter_memory CASCADE;
DROP TABLE IF EXISTS agent_steps CASCADE;
DROP TABLE IF EXISTS agent_runs CASCADE;
DROP TABLE IF EXISTS agent_defs CASCADE;
