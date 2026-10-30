-- 0009 live drafts: real-time co-editing working copy, AI suggestions, version contributors (Step 2c)
-- Requirements: DRF-08..11. Decision: ADR-020 (live co-editing over the version model of ADR-014).

-- The shared working copy of a draft as a Yjs (CRDT) document. Versions stay the immutable record;
-- this row is what everyone is typing into right now.
CREATE TABLE draft_live (
    draft_id         uuid PRIMARY KEY REFERENCES drafts(id) ON DELETE CASCADE,
    firm_id          uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    matter_id        uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    state            bytea NOT NULL,                -- full Yjs update encoding of the document
    contributors     uuid[] NOT NULL DEFAULT '{}',  -- who changed the text since the last saved version
    updated_at       timestamptz NOT NULL DEFAULT now()
);

-- An AI-proposed change to part of a draft, shown to every collaborator as a redline until a person
-- accepts or rejects it. Nothing the model writes reaches the draft without that decision.
CREATE TABLE draft_suggestions (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id       uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    matter_id     uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    draft_id      uuid NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    requested_by  uuid NOT NULL REFERENCES users(id),
    instruction   text NOT NULL,
    scope         text NOT NULL CHECK (scope IN ('selection', 'draft')),
    quote         text NOT NULL,          -- the text the suggestion replaces, as it was when asked
    start_hint    integer NOT NULL,       -- where that text was, to pick the right occurrence later
    replacement   text NOT NULL,
    explanation   text NOT NULL DEFAULT '',
    sources       jsonb NOT NULL DEFAULT '[]'::jsonb,  -- matter passages the model cited
    warnings      jsonb NOT NULL DEFAULT '[]'::jsonb,  -- e.g. figures not found in the text or the sources
    status        text NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'accepted', 'rejected', 'stale')),
    decided_by    uuid REFERENCES users(id),
    decided_at    timestamptz,
    model         text,
    cost_usd      numeric(12, 6) NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_draft_suggestions_draft ON draft_suggestions (draft_id, created_at);

-- Who wrote each version (everyone who typed or whose AI suggestion was accepted since the last one)
ALTER TABLE draft_versions ADD COLUMN contributors uuid[] NOT NULL DEFAULT '{}';

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['draft_live', 'draft_suggestions']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I USING (firm_id = app_firm_id()) WITH CHECK (firm_id = app_firm_id())',
            t);
    END LOOP;
END $$;

GRANT SELECT, INSERT, UPDATE ON draft_live, draft_suggestions TO app_user;
