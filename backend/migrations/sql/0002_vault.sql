-- 0002 vault: documents, versions, chunks (full-text), job payloads, job claiming
-- Requirements: VLT-01..08, VLT-11, NFR-09. Decisions: ADR-008 (FTS), ADR-009 (DB job queue).

ALTER TABLE jobs ADD COLUMN payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE jobs ADD COLUMN attempts integer NOT NULL DEFAULT 0;
CREATE INDEX ix_jobs_claim ON jobs (status, kind, created_at);

CREATE TABLE documents (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id             uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    matter_id           uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    title               text NOT NULL,
    filename_key        text NOT NULL,              -- lower-cased original filename, for versioning
    doc_type            text NOT NULL DEFAULT 'other',
    source              text NOT NULL DEFAULT 'upload' CHECK (source IN ('upload', 'email', 'portal', 'api')),
    status              text NOT NULL DEFAULT 'queued'
                        CHECK (status IN ('queued', 'processing', 'indexed', 'partial', 'failed')),
    current_version_id  uuid,
    page_count          integer,
    ocr_pending_pages   integer[] NOT NULL DEFAULT '{}',
    error               text,
    created_by          uuid NOT NULL REFERENCES users(id),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    deleted_at          timestamptz
);
CREATE INDEX ix_documents_matter ON documents (matter_id) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX ux_documents_matter_filename ON documents (matter_id, filename_key) WHERE deleted_at IS NULL;

CREATE TABLE document_versions (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id            uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    matter_id          uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    document_id        uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version_no         integer NOT NULL,
    storage_key        text NOT NULL,
    sha256             char(64) NOT NULL,
    mime               text NOT NULL,
    size_bytes         bigint NOT NULL,
    original_filename  text NOT NULL,
    created_by         uuid NOT NULL REFERENCES users(id),
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, version_no)
);
CREATE INDEX ix_versions_matter_sha ON document_versions (matter_id, sha256);
ALTER TABLE documents ADD CONSTRAINT fk_documents_current_version
    FOREIGN KEY (current_version_id) REFERENCES document_versions(id) DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE chunks (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id         uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    matter_id       uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    document_id     uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version_id      uuid NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    ordinal         integer NOT NULL,
    section_path    text,
    page_from       integer NOT NULL,
    page_to         integer NOT NULL,
    char_start      integer NOT NULL,
    char_end        integer NOT NULL,
    text            text NOT NULL,
    ocr_confidence  real,
    -- English stemming (weight A) + language-neutral tokens (weight B, covers Hindi and names)
    tsv             tsvector GENERATED ALWAYS AS (
                        setweight(to_tsvector('english'::regconfig, text), 'A') ||
                        setweight(to_tsvector('simple'::regconfig, text), 'B')
                    ) STORED,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_chunks_tsv ON chunks USING gin (tsv);
CREATE INDEX ix_chunks_document ON chunks (document_id, ordinal);
CREATE INDEX ix_chunks_matter ON chunks (matter_id);

-- ---------------------------------------------------------------- row-level security
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['documents', 'document_versions', 'chunks']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I USING (firm_id = app_firm_id()) WITH CHECK (firm_id = app_firm_id())',
            t);
    END LOOP;
END $$;

GRANT SELECT, INSERT, UPDATE ON documents, document_versions, chunks TO app_user;
GRANT DELETE ON chunks TO app_user;

-- ---------------------------------------------------------------- job claiming (ADR-009)
-- Workers have no firm context, so RLS hides every job from them. This SECURITY DEFINER function
-- (owned by the schema owner) claims exactly one job and returns its id and firm; the worker then
-- processes it inside an ordinary firm-scoped transaction. Stale running jobs are reclaimed.
CREATE OR REPLACE FUNCTION claim_next_job(p_kind text, p_stale_after interval DEFAULT interval '10 minutes')
RETURNS TABLE (job_id uuid, firm_id uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
    RETURN QUERY
    UPDATE jobs j
       SET status = 'running', attempts = j.attempts + 1, updated_at = now()
     WHERE j.id = (
            SELECT c.id FROM jobs c
             WHERE c.kind = p_kind
               AND (c.status = 'queued' OR (c.status = 'running' AND c.updated_at < now() - p_stale_after))
               AND c.attempts < 3
             ORDER BY c.created_at
             FOR UPDATE SKIP LOCKED
             LIMIT 1)
    RETURNING j.id, j.firm_id;
END $$;
REVOKE ALL ON FUNCTION claim_next_job(text, interval) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION claim_next_job(text, interval) TO app_user;
