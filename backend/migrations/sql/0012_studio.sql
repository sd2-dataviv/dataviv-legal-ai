-- 0012 drafting studio: general documents (contracts, legal notices, affidavits, letters...) written by
-- the model from a template and the lawyer's facts. Firm-wide history; never linked to a matter.

CREATE TABLE studio_documents (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id       uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    root_id       uuid REFERENCES studio_documents(id) ON DELETE CASCADE,   -- first version; NULL on it
    parent_id     uuid REFERENCES studio_documents(id) ON DELETE SET NULL,  -- the version this revises
    template      text NOT NULL,                        -- catalogue key, e.g. 'nda', 'notice_138'
    title         text NOT NULL CHECK (length(title) BETWEEN 1 AND 300),
    inputs        jsonb NOT NULL DEFAULT '{}'::jsonb,   -- the lawyer's answers to the template's fields
    instructions  text NOT NULL DEFAULT '',             -- free-text request, or the revision request
    content       text NOT NULL DEFAULT '',
    status        text NOT NULL DEFAULT 'streaming' CHECK (status IN ('streaming', 'complete', 'failed')),
    error         text,
    model         text,
    input_tokens  integer,
    output_tokens integer,
    latency_ms    integer,
    cost_usd      numeric(12, 6),
    created_by    uuid NOT NULL REFERENCES users(id),
    created_at    timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at    timestamptz NOT NULL DEFAULT clock_timestamp(),
    deleted_at    timestamptz,
    tsv           tsvector GENERATED ALWAYS AS (
                      to_tsvector('english'::regconfig, title || ' ' || content)
                      || to_tsvector('simple'::regconfig, title || ' ' || content)
                      || jsonb_to_tsvector('simple'::regconfig, inputs, '["string"]')  -- parties, facts
                  ) STORED
);
CREATE INDEX ix_studio_documents_firm ON studio_documents (firm_id, updated_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX ix_studio_documents_root ON studio_documents (root_id) WHERE root_id IS NOT NULL;
CREATE INDEX ix_studio_documents_tsv ON studio_documents USING gin (tsv);

ALTER TABLE studio_documents ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON studio_documents
    USING (firm_id = app_firm_id()) WITH CHECK (firm_id = app_firm_id());

-- No DELETE: documents are soft-deleted (deleted_at) so the audit trail keeps its objects.
GRANT SELECT, INSERT, UPDATE ON studio_documents TO app_user;
