-- 0010 e-signature: send an approved draft for signature (DocuSign), track every signer, keep the
-- signed PDF and the certificate of completion in the matter's Vault (Step 2e, ADR-021). DRF-12..15.

-- Signed copies and certificates are stored as Vault documents with source 'esign'.
ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_source_check;
ALTER TABLE documents ADD CONSTRAINT documents_source_check
    CHECK (source IN ('upload', 'email', 'portal', 'api', 'esign'));

-- One row per envelope sent to the signing provider. The version sent is fixed: it is always the
-- draft's approved version at the time of sending, and later edits to the draft never change it.
CREATE TABLE signature_requests (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id                  uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    matter_id                uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    draft_id                 uuid NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    version_no               integer NOT NULL,
    provider                 text NOT NULL CHECK (provider IN ('docusign', 'fake')),
    envelope_id              text NOT NULL,
    status                   text NOT NULL DEFAULT 'sent'
                             CHECK (status IN ('sent', 'delivered', 'completed', 'declined', 'voided', 'failed')),
    subject                  text NOT NULL,
    message                  text,
    sent_by                  uuid NOT NULL REFERENCES users(id),
    sent_at                  timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at             timestamptz,
    voided_reason            text,
    last_checked_at          timestamptz,
    last_error               text,
    signed_document_id       uuid REFERENCES documents(id),
    certificate_document_id  uuid REFERENCES documents(id),
    updated_at               timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ux_signature_requests_envelope ON signature_requests (provider, envelope_id);
CREATE INDEX ix_signature_requests_draft ON signature_requests (draft_id, sent_at);
-- At most one envelope out for signature per draft at a time
CREATE UNIQUE INDEX ux_signature_requests_open ON signature_requests (draft_id)
    WHERE status IN ('sent', 'delivered');

CREATE TABLE signature_signers (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id        uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    request_id     uuid NOT NULL REFERENCES signature_requests(id) ON DELETE CASCADE,
    recipient_id   text NOT NULL,              -- the provider's recipient id ("1", "2", ...)
    name           text NOT NULL,
    email          text NOT NULL,
    routing_order  integer NOT NULL,
    user_id        uuid REFERENCES users(id),  -- set when the signer is a member of the firm
    status         text NOT NULL DEFAULT 'sent'
                   CHECK (status IN ('created', 'sent', 'delivered', 'signed', 'declined', 'voided')),
    signed_at      timestamptz,
    declined_reason text
);
CREATE INDEX ix_signature_signers_request ON signature_signers (request_id, routing_order);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['signature_requests', 'signature_signers']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I USING (firm_id = app_firm_id()) WITH CHECK (firm_id = app_firm_id())',
            t);
    END LOOP;
END $$;

GRANT SELECT, INSERT, UPDATE ON signature_requests, signature_signers TO app_user;

-- Provider callbacks (DocuSign Connect) and the background status sweep have no firm context, so RLS
-- hides every request from them. These SECURITY DEFINER functions return only ids and firms; the
-- work itself then happens inside an ordinary firm-scoped transaction (the pattern of claim_next_job).
CREATE OR REPLACE FUNCTION esign_request_firm(p_provider text, p_envelope_id text)
RETURNS TABLE (request_id uuid, firm_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT r.id, r.firm_id FROM signature_requests r
     WHERE r.provider = p_provider AND r.envelope_id = p_envelope_id
$$;

CREATE OR REPLACE FUNCTION esign_open_requests(p_stale_after interval, p_limit integer DEFAULT 50)
RETURNS TABLE (request_id uuid, firm_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT r.id, r.firm_id FROM signature_requests r
     WHERE r.status IN ('sent', 'delivered')
       AND (r.last_checked_at IS NULL OR r.last_checked_at < now() - p_stale_after)
     ORDER BY r.last_checked_at NULLS FIRST
     LIMIT p_limit
$$;

REVOKE ALL ON FUNCTION esign_request_firm(text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION esign_open_requests(interval, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION esign_request_firm(text, text) TO app_user;
GRANT EXECUTE ON FUNCTION esign_open_requests(interval, integer) TO app_user;
