-- 0001 foundation: tenancy, users, matters, walls, jobs, audit
-- Requirements: FND-01, FND-03, FND-04, FND-05, NFR-09
-- Runs as the schema owner. The application connects as app_user, which is NOT the
-- owner, so row-level security always applies to it (ADR-002).

-- gen_random_uuid() is built into PostgreSQL 13+; no extension needed.

-- Helper: the firm of the current transaction (NULL when unset -> policies match nothing)
CREATE OR REPLACE FUNCTION app_firm_id() RETURNS uuid
LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('app.firm_id', true), '')::uuid
$$;

CREATE TABLE firms (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name             text NOT NULL,
    deployment_mode  text NOT NULL DEFAULT 'saas'
                     CHECK (deployment_mode IN ('saas', 'dedicated', 'on_prem', 'air_gapped')),
    model_policy     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id     uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    email       text NOT NULL,
    name        text NOT NULL,
    role        text NOT NULL
                CHECK (role IN ('partner', 'associate', 'junior', 'paralegal', 'admin', 'client')),
    is_active   boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (firm_id, email)
);

CREATE TABLE matters (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id      uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    title        text NOT NULL,
    client_name  text NOT NULL,
    matter_type  text NOT NULL DEFAULT 'litigation',
    forum        text,                -- e.g. 'Delhi High Court'
    cnr          text,                -- 16-char eCourts Case Number Record
    practice_area text,
    status       text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'on_hold', 'closed')),
    created_by   uuid NOT NULL REFERENCES users(id),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT cnr_format CHECK (cnr IS NULL OR cnr ~ '^[A-Z]{4}[0-9]{12}$')
);
CREATE INDEX ix_matters_firm ON matters (firm_id);

CREATE TABLE matter_members (
    matter_id    uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    firm_id      uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    matter_role  text NOT NULL CHECK (matter_role IN ('lead', 'partner', 'member', 'viewer')),
    added_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (matter_id, user_id)
);
CREATE INDEX ix_matter_members_user ON matter_members (user_id);

CREATE TABLE ethical_walls (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id     uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    matter_id   uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    reason      text NOT NULL,
    created_by  uuid NOT NULL REFERENCES users(id),
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, matter_id)
);

CREATE TABLE jobs (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id       uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    matter_id     uuid REFERENCES matters(id) ON DELETE CASCADE,
    kind          text NOT NULL,           -- ingest | review_table | workflow | agent
    status        text NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued', 'running', 'waiting_approval', 'succeeded', 'failed', 'cancelled')),
    total         integer NOT NULL DEFAULT 0,
    done          integer NOT NULL DEFAULT 0,
    current_step  text,
    error         text,
    created_by    uuid NOT NULL REFERENCES users(id),
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE audit_events (
    id           bigserial PRIMARY KEY,
    firm_id      uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    actor_id     uuid,
    action       text NOT NULL,            -- e.g. matter.create, matter.access_denied, ai.query
    object_type  text NOT NULL,
    object_id    text,
    matter_id    uuid,
    detail       jsonb NOT NULL DEFAULT '{}'::jsonb,
    prev_hash    text NOT NULL,
    hash         text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_audit_firm_id ON audit_events (firm_id, id);

-- ---------------------------------------------------------------- row-level security
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['users', 'matters', 'matter_members', 'ethical_walls', 'jobs', 'audit_events']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I USING (firm_id = app_firm_id()) WITH CHECK (firm_id = app_firm_id())',
            t);
    END LOOP;
END $$;

ALTER TABLE firms ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON firms USING (id = app_firm_id());

-- ---------------------------------------------------------------- grants for app_user
-- app_user is created by infra/postgres/init.sql (or the DBA in production).
GRANT USAGE ON SCHEMA public TO app_user;
GRANT SELECT ON firms TO app_user;
GRANT SELECT, INSERT, UPDATE ON users, matters, matter_members, ethical_walls, jobs TO app_user;
GRANT DELETE ON matter_members, ethical_walls TO app_user;
-- audit is append-only for the application: no UPDATE or DELETE
GRANT SELECT, INSERT ON audit_events TO app_user;
GRANT USAGE ON SEQUENCE audit_events_id_seq TO app_user;
GRANT EXECUTE ON FUNCTION app_firm_id() TO app_user;
