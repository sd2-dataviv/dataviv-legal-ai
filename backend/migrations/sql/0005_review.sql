-- 0005 review tables: ask the same questions of many documents; one cited answer per cell.
-- Requirements: VLT-09, VLT-10, NFR-09. Decisions: ADR-015 (one model call per document, sliced jobs).

CREATE TABLE review_tables (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id     uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    matter_id   uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    title       text NOT NULL,
    job_id      uuid REFERENCES jobs(id) ON DELETE SET NULL,   -- latest run
    created_by  uuid NOT NULL REFERENCES users(id),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_review_tables_matter ON review_tables (matter_id, updated_at DESC);

CREATE TABLE review_columns (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id      uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    matter_id    uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    table_id     uuid NOT NULL REFERENCES review_tables(id) ON DELETE CASCADE,
    position     integer NOT NULL,
    label        text NOT NULL,
    question     text NOT NULL,
    answer_type  text NOT NULL DEFAULT 'text' CHECK (answer_type IN ('text', 'yes_no', 'date', 'amount', 'choice')),
    choices      jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_by   uuid NOT NULL REFERENCES users(id),
    created_at   timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ix_review_columns_table ON review_columns (table_id, position);

CREATE TABLE review_rows (
    firm_id      uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    matter_id    uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    table_id     uuid NOT NULL REFERENCES review_tables(id) ON DELETE CASCADE,
    document_id  uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    position     integer NOT NULL,
    added_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (table_id, document_id)
);

CREATE TABLE review_cells (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id        uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    matter_id      uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    table_id       uuid NOT NULL REFERENCES review_tables(id) ON DELETE CASCADE,
    column_id      uuid NOT NULL REFERENCES review_columns(id) ON DELETE CASCADE,
    document_id    uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    status         text NOT NULL DEFAULT 'empty'
                   CHECK (status IN ('empty', 'queued', 'done', 'not_found', 'failed')),
    answer         text,          -- what the table shows (a person's correction replaces the model's)
    model_answer   text,          -- what the model said, kept even after a correction
    value          text,          -- normalised: ISO date, amount in rupees, yes/no, a choice
    quote          text,          -- the exact words supporting the answer
    chunk_id       uuid REFERENCES chunks(id) ON DELETE SET NULL,
    page_from      integer,
    page_to        integer,
    verified       boolean,       -- quote found verbatim in the passage and every figure matches
    flag           text,          -- why it is not verified, if it is not
    error          text,
    model          text,
    input_tokens   integer,
    output_tokens  integer,
    cost_usd       numeric(12, 6),
    edited_by      uuid REFERENCES users(id),
    edited_at      timestamptz,
    reviewed_by    uuid REFERENCES users(id),
    reviewed_at    timestamptz,
    updated_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (column_id, document_id)
);
CREATE INDEX ix_review_cells_table ON review_cells (table_id, document_id);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['review_tables', 'review_columns', 'review_rows', 'review_cells']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I USING (firm_id = app_firm_id()) WITH CHECK (firm_id = app_firm_id())',
            t);
    END LOOP;
END $$;

GRANT SELECT, INSERT, UPDATE, DELETE ON review_tables, review_columns, review_rows, review_cells TO app_user;
