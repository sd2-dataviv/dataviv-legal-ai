-- 0003 assistant: matter-scoped threads and messages, searchable history, firm model policy editing.
-- Requirements: AST-01..04, AST-06, AST-08, AST-10, FND-06. Decisions: ADR-012 (gateway), ADR-013 (verifier).

CREATE TABLE threads (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id     uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    matter_id   uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    title       text NOT NULL,
    created_by  uuid NOT NULL REFERENCES users(id),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_threads_matter ON threads (matter_id, updated_at DESC);

CREATE TABLE thread_messages (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id       uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    matter_id     uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    thread_id     uuid NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    role          text NOT NULL CHECK (role IN ('user', 'assistant')),
    content       text NOT NULL DEFAULT '',
    mode          text NOT NULL DEFAULT 'ask' CHECK (mode IN ('ask', 'summarise', 'draft')),
    options       jsonb NOT NULL DEFAULT '{}'::jsonb,   -- draft kind, chosen documents, requested model
    sources       jsonb NOT NULL DEFAULT '[]'::jsonb,   -- S1..Sn -> chunk, document, pages
    sentences     jsonb NOT NULL DEFAULT '[]'::jsonb,   -- parsed answer: text, citations, verifier label
    verification  jsonb NOT NULL DEFAULT '{}'::jsonb,   -- counts per label, verifier used
    status        text NOT NULL DEFAULT 'complete' CHECK (status IN ('streaming', 'complete', 'failed')),
    error         text,
    model         text,
    input_tokens  integer,
    output_tokens integer,
    latency_ms    integer,
    cost_usd      numeric(12, 6),
    created_by    uuid NOT NULL REFERENCES users(id),
    -- clock_timestamp: a question and its answer are inserted in one transaction but must sort in order
    created_at    timestamptz NOT NULL DEFAULT clock_timestamp(),
    tsv           tsvector GENERATED ALWAYS AS (
                      to_tsvector('english'::regconfig, content) || to_tsvector('simple'::regconfig, content)
                  ) STORED
);
CREATE INDEX ix_thread_messages_thread ON thread_messages (thread_id, created_at);
CREATE INDEX ix_thread_messages_tsv ON thread_messages USING gin (tsv);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['threads', 'thread_messages']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I USING (firm_id = app_firm_id()) WITH CHECK (firm_id = app_firm_id())',
            t);
    END LOOP;
END $$;

GRANT SELECT, INSERT, UPDATE ON threads, thread_messages TO app_user;
-- Firm admins edit their own firm's model policy (RLS limits UPDATE to the caller's firm row).
GRANT UPDATE (model_policy) ON firms TO app_user;
