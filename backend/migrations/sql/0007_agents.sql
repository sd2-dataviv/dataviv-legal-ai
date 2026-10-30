-- 0007 agents: versioned definitions, runs with a full step trace, matter memory; approvals cover agents too.
-- Requirements: AGT-01..07. Decisions: ADR-018 (one structured decision per step), ADR-017 (DB-durable runs).

CREATE TABLE agent_defs (
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

CREATE TABLE agent_runs (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id            uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    matter_id          uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    parent_run_id      uuid REFERENCES agent_runs(id) ON DELETE CASCADE,
    slug               text NOT NULL,
    version            integer NOT NULL,
    name               text NOT NULL,
    spec               jsonb NOT NULL,                 -- snapshot: instructions, tools, policy, budgets
    goal               text NOT NULL,
    scope_document_ids uuid[],                         -- NULL = the whole matter
    status             text NOT NULL DEFAULT 'queued'
                       CHECK (status IN ('queued', 'running', 'waiting_approval', 'waiting_subagents',
                                         'succeeded', 'stopped', 'failed', 'cancelled')),
    max_steps          integer NOT NULL,
    max_cost_usd       numeric(12, 6) NOT NULL,
    steps_used         integer NOT NULL DEFAULT 0,
    sources            jsonb NOT NULL DEFAULT '[]'::jsonb,   -- passages seen: [{key: S1, chunk_id, document_id, ...}]
    answer             text,
    sentences          jsonb NOT NULL DEFAULT '[]'::jsonb,
    verification       jsonb NOT NULL DEFAULT '{}'::jsonb,
    job_id             uuid REFERENCES jobs(id),
    error              text,
    input_tokens       integer NOT NULL DEFAULT 0,
    output_tokens      integer NOT NULL DEFAULT 0,
    cost_usd           numeric(12, 6) NOT NULL DEFAULT 0,   -- including subagents
    created_by         uuid NOT NULL REFERENCES users(id),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    finished_at        timestamptz
);
CREATE INDEX ix_agent_runs_matter ON agent_runs (matter_id, created_at DESC);
CREATE INDEX ix_agent_runs_parent ON agent_runs (parent_run_id);

CREATE TABLE agent_steps (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id        uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    matter_id      uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    run_id         uuid NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    position       integer NOT NULL,
    thought        text,
    tool           text NOT NULL,
    arguments      jsonb NOT NULL DEFAULT '{}'::jsonb,
    status         text NOT NULL
                   CHECK (status IN ('running', 'waiting_approval', 'approved', 'waiting', 'done', 'rejected', 'failed')),
    observation    text,
    result         jsonb NOT NULL DEFAULT '{}'::jsonb,
    model          text,
    input_tokens   integer NOT NULL DEFAULT 0,
    output_tokens  integer NOT NULL DEFAULT 0,
    cost_usd       numeric(12, 6) NOT NULL DEFAULT 0,
    error          text,
    created_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at    timestamptz,
    UNIQUE (run_id, position)
);

-- Matter memory (AGT-05): facts, events, parties, deadlines and notes, each with its source passage.
CREATE TABLE matter_memory (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id       uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    matter_id     uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    kind          text NOT NULL CHECK (kind IN ('fact', 'event', 'party', 'deadline', 'note')),
    subject       text NOT NULL,
    content       text NOT NULL,
    event_date    date,
    document_id   uuid REFERENCES documents(id) ON DELETE SET NULL,
    chunk_id      uuid REFERENCES chunks(id) ON DELETE SET NULL,
    page_from     integer,
    page_to       integer,
    source        text NOT NULL DEFAULT 'user' CHECK (source IN ('user', 'agent')),
    agent_run_id  uuid REFERENCES agent_runs(id) ON DELETE SET NULL,
    created_by    uuid NOT NULL REFERENCES users(id),
    created_at    timestamptz NOT NULL DEFAULT clock_timestamp(),
    deleted_at    timestamptz,
    deleted_by    uuid REFERENCES users(id),
    tsv           tsvector GENERATED ALWAYS AS (
                      to_tsvector('simple', coalesce(subject, '') || ' ' || coalesce(content, ''))) STORED
);
CREATE INDEX ix_matter_memory_matter ON matter_memory (matter_id, created_at) WHERE deleted_at IS NULL;
CREATE INDEX ix_matter_memory_tsv ON matter_memory USING gin (tsv);

-- One approvals table for workflow steps and agent actions, so approvers have one queue.
ALTER TABLE approvals ALTER COLUMN run_id DROP NOT NULL;
ALTER TABLE approvals ALTER COLUMN step_id DROP NOT NULL;
ALTER TABLE approvals ADD COLUMN agent_run_id uuid REFERENCES agent_runs(id) ON DELETE CASCADE;
ALTER TABLE approvals ADD COLUMN agent_step_id uuid REFERENCES agent_steps(id) ON DELETE CASCADE;
ALTER TABLE approvals ADD COLUMN detail jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE approvals ADD CONSTRAINT approvals_one_subject CHECK (
    (run_id IS NOT NULL AND step_id IS NOT NULL AND agent_run_id IS NULL AND agent_step_id IS NULL)
    OR (run_id IS NULL AND step_id IS NULL AND agent_run_id IS NOT NULL AND agent_step_id IS NOT NULL));
ALTER TABLE approvals DROP CONSTRAINT approvals_required_role_check;
ALTER TABLE approvals ADD CONSTRAINT approvals_required_role_check
    CHECK (required_role IN ('matter_editor', 'matter_manager', 'firm_partner'));
CREATE UNIQUE INDEX ux_approvals_one_pending_per_agent_step ON approvals (agent_step_id) WHERE status = 'pending';

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['agent_defs', 'agent_runs', 'agent_steps', 'matter_memory']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I USING (firm_id = app_firm_id()) WITH CHECK (firm_id = app_firm_id())',
            t);
    END LOOP;
END $$;

GRANT SELECT, INSERT, UPDATE ON agent_runs, agent_steps, matter_memory TO app_user;
GRANT SELECT, INSERT ON agent_defs TO app_user;   -- append-only: edits are new versions
