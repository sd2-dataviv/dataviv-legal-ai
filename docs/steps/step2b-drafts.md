# Step 2b — Drafts (edit, version, approve, collaborate)

**Implements:** DRF-01..07 · **Status:** done, 10 October 2026 — 80/80 tests passing on Windows (8 for drafts)

## Scope

| In this step | Deferred (and why) |
|---|---|
| Open any Assistant answer as a draft ("Edit as draft"), or start a blank one | Rich text (bold, tables, numbering styles) → plain text keeps diffs exact; formatting arrives with a document editor |
| Every save is a numbered, immutable version with a note; compare any two versions word by word; restore | Live keystroke-by-keystroke co-editing → ADR-014 |
| Conflict-safe saves: 409 when someone saved first, then compare / save on top / reload | E-mail or in-app notifications → with the notifications service; the console shows "to approve" counts |
| Approval by a lead or partner on the matter (four eyes: never your own request); locked while waiting; changes requested need a note | Multi-step approval chains → Workflows (Step 4) |
| Comments with quoted passages; resolve | Comment threads / replies |
| Presence: who has the draft open and who is editing (5-second heartbeats) | |
| Activity feed from the audit trail; Word export stamped approved / not approved | |

## Who may do what

| Action | viewer | member | partner / lead |
|---|:-:|:-:|:-:|
| Read, compare versions, comment | ✓ | ✓ | ✓ |
| Create, edit, restore, ask for approval | | ✓ | ✓ |
| Approve or request changes | | | ✓ when chosen, never their own request |

Firm admins, walled users and other firms get 404, as for every matter object (ADR-007).

## Decision

**ADR-014 — Versioned saves with conflict detection, not live co-editing (yet).** Real-time co-typing needs a CRDT (Yjs) and a sync server; the Python side (pycrdt / y-py) is a compiled extension that Windows Smart App Control blocks (ADR-011), and legal drafting needs deliberate, attributable versions more than simultaneous typing. Each save names the version it started from; the database row lock guarantees version numbers are never shared, history is append-only (`app_user` has no UPDATE or DELETE on `draft_versions`), and presence tells colleagues who is working. A live co-editing layer can later sit on top without changing the version model.

## Lifecycle

```
draft ──ask for approval──▶ in_review ──approve──▶ approved
  ▲                           │   │                   │
  │◀──────── withdraw ────────┘   └─request changes─▶ changes_requested
  │                                                   │
  └────────── any save after approval ◀───────────────┘ (approved version stays on record)
```

## API

| Method & path | Purpose |
|---|---|
| `POST /v1/matters/{id}/drafts` | `{title?, kind?, content?, from_message_id?}` |
| `GET /v1/matters/{id}/drafts` · `GET /v1/drafts/awaiting-me` | Lists; "awaiting me" spans every matter you can open |
| `GET /v1/drafts/{id}` | Text, versions, reviews, comments, presence, activity |
| `PATCH /v1/drafts/{id}` | Title, type |
| `POST /v1/drafts/{id}/versions` | `{content, base_version_no, note?, force?}` → 201, or 409 if someone saved first |
| `GET /v1/drafts/{id}/versions/{n}` · `POST …/versions/{n}/restore` | View; copy forward |
| `GET /v1/drafts/{id}/diff?from=&to=` | Word-level changes |
| `POST /v1/drafts/{id}/review` · `…/review/decision` · `…/review/withdraw` | Approval |
| `POST /v1/drafts/{id}/comments` · `…/comments/{cid}/resolve` | Comments |
| `POST /v1/drafts/{id}/presence` | Heartbeat; returns newest version and who else is here |
| `GET /v1/drafts/{id}/export?version=` | Word, stamped |
