# Step 2f — Shared API on WorkflowHub

Other WorkflowHub apps and AI agents can call Dataviv AI OS **as the person asking**.

## What is shared
`platform/node.yaml` → `api:` (base path `/v1`), 13 endpoints: `whoami`, `list_matters`, `get_matter`,
`list_documents`, `search_documents`, `start_thread`, `ask_assistant`, `list_drafts`, `get_draft`, `create_draft`,
`drafts_awaiting_me`, `list_approvals`, `signature_status`. Nothing that deletes, changes firm settings or sends
for signature is shared.

## Who the caller is (`backend/app/core/platform.py`, `app/api/deps.py`)
1. `X-Wfh-User` — added by the platform to calls from other apps / agents: HS256 JWT, issuer `workflowhub`,
   audience `WFH_APP_API_AUDIENCE`, signed with `WFH_APP_API_SECRET` (the platform sets both). Forged → 401.
2. Our own bearer token (console, dev identities, OIDC) — unchanged.
3. `X-Auth-Token` — a person's platform token (scripts, Postman), checked with `WFH_AUTH_SERVICE_URL/tokens/details`,
   cached 60 s.

The caller becomes the **Dataviv user with the same e-mail** (`platform_user_by_email()`, migration 0011, a
SECURITY DEFINER lookup like `esign_request_firm`), inside that user's firm and role, so RLS, matter membership
and ethical walls apply. An e-mail in two firms → 403 (ambiguous). No Dataviv account → 403, except in dev/test
where `DATAVIV_PLATFORM_FALLBACK_EMAIL` (platform compose default: the demo partner) is used instead.

## After upload
App → Sharing → Agent access (MCP) → **Expose to agents** (off = every call refused). The API tab then lists each
endpoint's URL, a curl command and the `uses:` block other apps paste into their node.yaml.

## Also in this step
- The search model preloads in the background when the API starts (`DATAVIV_EMBEDDINGS_PRELOAD`, default true):
  the first search / agent call no longer waits ~90 s for it.
- `dataviv.*` log lines go to stdout at INFO (the platform's Logs tab shows only stdout), e.g.
  `[platform] shared API call by platform user 42 as asha@…`. Tokens are never logged.

Tests: `tests/test_platform_api.py` (8).
