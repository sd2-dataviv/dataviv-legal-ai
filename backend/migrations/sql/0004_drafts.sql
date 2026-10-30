-- 0004 drafts: editable, versioned, approvable documents shared by a matter's members.
-- Requirements: DRF-01..07. Decisions: ADR-014 (versioned saves with conflict check, not live co-editing).

CREATE TABLE drafts (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id              uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    matter_id            uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    title                text NOT NULL,
    kind                 text NOT NULL DEFAULT 'other' CHECK (kind IN ('notice', 'reply', 'email', 'memo', 'other')),
    status               text NOT NULL DEFAULT 'draft'
                         CHECK (status IN ('draft', 'in_review', 'changes_requested', 'approved')),
    current_version_no   integer NOT NULL DEFAULT 1,
    approved_version_no  integer,
    approved_by          uuid REFERENCES users(id),
    approved_at          timestamptz,
    source_message_id    uuid REFERENCES thread_messages(id) ON DELETE SET NULL,
    sources              jsonb NOT NULL DEFAULT '[]'::jsonb,  -- citations [1]..[n] carried over from the Assistant
    created_by           uuid NOT NULL REFERENCES users(id),
    updated_by           uuid NOT NULL REFERENCES users(id),
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_drafts_matter ON drafts (matter_id, updated_at DESC);

-- Append-only history: app_user may insert and read versions, never change or delete them.
CREATE TABLE draft_versions (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id     uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    matter_id   uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    draft_id    uuid NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    version_no  integer NOT NULL,
    content     text NOT NULL,
    note        text,
    created_by  uuid NOT NULL REFERENCES users(id),
    created_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (draft_id, version_no)
);

CREATE TABLE draft_reviews (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id        uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    matter_id      uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    draft_id       uuid NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    version_no     integer NOT NULL,
    requested_by   uuid NOT NULL REFERENCES users(id),
    reviewer_id    uuid NOT NULL REFERENCES users(id),
    status         text NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'approved', 'changes_requested', 'withdrawn')),
    message        text,
    decision_note  text,
    decided_by     uuid REFERENCES users(id),
    decided_at     timestamptz,
    created_at     timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE UNIQUE INDEX ux_draft_reviews_one_pending ON draft_reviews (draft_id) WHERE status = 'pending';
CREATE INDEX ix_draft_reviews_reviewer ON draft_reviews (reviewer_id) WHERE status = 'pending';

CREATE TABLE draft_comments (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id      uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    matter_id    uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    draft_id     uuid NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    version_no   integer NOT NULL,
    author_id    uuid NOT NULL REFERENCES users(id),
    body         text NOT NULL,
    quote        text,                 -- the passage the comment is about, if any
    resolved_by  uuid REFERENCES users(id),
    resolved_at  timestamptz,
    created_at   timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ix_draft_comments_draft ON draft_comments (draft_id, created_at);

-- Who has the draft open right now (heartbeats every few seconds; stale rows are simply ignored).
CREATE TABLE draft_presence (
    firm_id    uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    matter_id  uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    draft_id   uuid NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    user_id    uuid NOT NULL REFERENCES users(id),
    editing    boolean NOT NULL DEFAULT false,
    seen_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (draft_id, user_id)
);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['drafts', 'draft_versions', 'draft_reviews', 'draft_comments', 'draft_presence']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I USING (firm_id = app_firm_id()) WITH CHECK (firm_id = app_firm_id())',
            t);
    END LOOP;
END $$;

GRANT SELECT, INSERT, UPDATE ON drafts, draft_reviews, draft_comments, draft_presence TO app_user;
GRANT SELECT, INSERT ON draft_versions TO app_user;
