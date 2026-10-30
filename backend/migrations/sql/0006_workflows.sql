-- 0006 workflows: playbooks, versioned definitions, durable runs with step records and approvals.
-- Requirements: WFL-01..07. Decisions: ADR-017 (DB-backed durable runs instead of Temporal for now).

CREATE TABLE playbooks (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id        uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    name           text NOT NULL,
    contract_type  text NOT NULL DEFAULT 'general',
    description    text,
    clauses        jsonb NOT NULL DEFAULT '[]'::jsonb,   -- [{key, title, question, preferred, fallback, unacceptable, guidance}]
    version        integer NOT NULL DEFAULT 1,
    created_by     uuid NOT NULL REFERENCES users(id),
    updated_by     uuid NOT NULL REFERENCES users(id),
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);

-- Firm-authored definitions (built-in ones ship with the code). Every edit is a new version.
CREATE TABLE workflow_defs (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id     uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    slug        text NOT NULL,
    version     integer NOT NULL,
    name        text NOT NULL,
    spec        jsonb NOT NULL,
    created_by  uuid NOT NULL REFERENCES users(id),
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (firm_id, slug, version)
);

CREATE TABLE workflow_runs (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id        uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    matter_id      uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    slug           text NOT NULL,
    version        integer NOT NULL,
    name           text NOT NULL,
    spec           jsonb NOT NULL,                         -- snapshot: editing a definition never changes a run
    inputs         jsonb NOT NULL DEFAULT '{}'::jsonb,
    status         text NOT NULL DEFAULT 'queued'
                   CHECK (status IN ('queued', 'running', 'waiting_approval', 'succeeded', 'failed', 'rejected', 'cancelled')),
    job_id         uuid REFERENCES jobs(id) ON DELETE SET NULL,
    error          text,
    input_tokens   integer NOT NULL DEFAULT 0,
    output_tokens  integer NOT NULL DEFAULT 0,
    cost_usd       numeric(12, 6) NOT NULL DEFAULT 0,
    created_by     uuid NOT NULL REFERENCES users(id),
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz
);
CREATE INDEX ix_workflow_runs_matter ON workflow_runs (matter_id, created_at DESC);

CREATE TABLE workflow_steps (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id        uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    matter_id      uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    run_id         uuid NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
    position       integer NOT NULL,
    step_key       text NOT NULL,
    type           text NOT NULL,
    title          text NOT NULL,
    status         text NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'running', 'waiting', 'done', 'failed', 'skipped')),
    resolved       jsonb NOT NULL DEFAULT '{}'::jsonb,     -- the step's parameters after templating (its inputs)
    output         jsonb NOT NULL DEFAULT '{}'::jsonb,
    model          text,
    input_tokens   integer NOT NULL DEFAULT 0,
    output_tokens  integer NOT NULL DEFAULT 0,
    cost_usd       numeric(12, 6) NOT NULL DEFAULT 0,
    error          text,
    attempts       integer NOT NULL DEFAULT 0,
    started_at     timestamptz,
    finished_at    timestamptz,
    UNIQUE (run_id, position)
);

CREATE TABLE approvals (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id        uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    matter_id      uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    run_id         uuid NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
    step_id        uuid NOT NULL REFERENCES workflow_steps(id) ON DELETE CASCADE,
    required_role  text NOT NULL CHECK (required_role IN ('matter_manager', 'firm_partner')),
    message        text,
    status         text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled')),
    requested_by   uuid NOT NULL REFERENCES users(id),
    decided_by     uuid REFERENCES users(id),
    decided_at     timestamptz,
    comment        text,
    created_at     timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE UNIQUE INDEX ux_approvals_one_pending_per_step ON approvals (step_id) WHERE status = 'pending';

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['playbooks', 'workflow_defs', 'workflow_runs', 'workflow_steps', 'approvals']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I USING (firm_id = app_firm_id()) WITH CHECK (firm_id = app_firm_id())',
            t);
    END LOOP;
END $$;

GRANT SELECT, INSERT, UPDATE ON playbooks, workflow_runs, workflow_steps, approvals TO app_user;
GRANT DELETE ON playbooks TO app_user;
GRANT SELECT, INSERT ON workflow_defs TO app_user;   -- definitions are append-only: edits are new versions
