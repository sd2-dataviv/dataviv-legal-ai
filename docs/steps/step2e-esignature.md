# Step 2e: E-signature of approved drafts (DocuSign)

**Implements:** DRF-12..15 · **Decision:** ADR-021 · **Status:** done, 19 October 2026. 148 tests on Linux (8 for e-signature, including the DocuSign client against a mock DocuSign server); ruff and mypy clean. The flow was also checked in the console in test mode.

Once a draft is approved, the matter lead or a partner can send that approved version for signature. Signers can be colleagues, the client or the other side. They sign in order, through DocuSign. Dataviv follows each signer's status. When everyone has signed, it stores the signed PDF and DocuSign's certificate of completion in the matter's Vault, indexed and searchable like any upload. Every step is recorded in the audit log and shown as draft activity.

## How it works

```
 Signature tab ──POST /v1/drafts/{id}/signatures──► esign service ──► DocuSign eSignature REST v2.1
   (approved vN,         (lead / partner only,           │  Word file of vN with a signature block;
    signers, order)       IT Act exclusions confirmed)   │  white anchors /dvsigN/ /dvdateN/ place the fields
                                                          ▼
                              signature_requests + signature_signers (RLS)
                                                          ▲
   status comes back by:  DocuSign Connect callback (HMAC) · background sweep every 60 s · "Check status"
                                                          │
                       completed ──► signed PDF + certificate ──► Vault (source "esign", ingested)
```

- **What is signed.** Only the approved version is sent, as a Word file stamped *approved by …*, with a signature block for each signer. Each signature and date field sits on invisible white anchor text. Later edits to the draft never change what was sent. The record keeps the version number, and the console warns when the draft has moved on since approval.
- **Signing order.** Signers with the same order sign in parallel; a higher number waits for the lower ones. Signers who are members of the firm are linked to their user by e-mail address.
- **One envelope at a time.** A draft can have only one envelope out for signature. A declined or voided envelope can be sent again.
- **Status.** DocuSign Connect calls `POST /v1/esign/docusign/connect`, signed with HMAC, when `DATAVIV_DOCUSIGN_WEBHOOK_URL` is set to a public HTTPS address. Without that, the worker checks open envelopes every `DATAVIV_ESIGN_POLL_SECONDS` (60), and anyone on the matter can press **Check status**. Callbacks and the sweep have no firm context, so two `SECURITY DEFINER` functions return only ids and firms. The work then happens in an ordinary firm-scoped transaction, on behalf of the person who sent the envelope.
- **Indian law.** The sender must confirm the document is not one the IT Act 2000 (First Schedule) excludes from e-signature: negotiable instruments other than cheques, powers of attorney, trust deeds, wills, and contracts for the sale or conveyance of immovable property. A standard DocuSign signature is an electronic record under the Indian Contract Act. It is not an IT Act "electronic signature" (Aadhaar eSign or a DSC). Aadhaar-level signing needs DocuSign's eMudhra integration, which DocuSign switches on by support case. Stamp duty still applies where state law requires it. This is not legal advice.

## Who may do what

| | viewer | member | lead / partner |
|---|:-:|:-:|:-:|
| See envelopes, signers and status; check status | ✓ | ✓ | ✓ |
| Act as a signer in test mode | | ✓ | ✓ |
| Send for signature, void | | | ✓ |

Walled users, non-members and other firms get 404, as for every draft endpoint.

## API

| Method & path | Purpose |
|---|---|
| `GET /v1/esign/status` | Provider, account and whether sending works now; when it does not, what to fix and the DocuSign consent link |
| `POST /v1/drafts/{id}/signatures` | `{signers: [{name, email, routing_order}], subject?, message?, confirm_not_excluded}` → the envelope |
| `GET /v1/drafts/{id}/signatures` | Envelopes of the draft, newest first (also in `GET /v1/drafts/{id}` as `signatures`) |
| `POST /v1/signatures/{id}/refresh` | Ask DocuSign for the latest status now |
| `POST /v1/signatures/{id}/void` | `{reason}`: cancel an envelope still out for signature |
| `POST /v1/esign/docusign/connect` | DocuSign Connect callback, authenticated by `X-DocuSign-Signature-n` (HMAC-SHA256) |
| `POST /v1/dev/signatures/{id}/simulate` | Test mode only: `{action: deliver / sign_next / sign_all / decline}` |

## Settings (`backend/.env`)

| Setting | Meaning |
|---|---|
| `DATAVIV_ESIGN_BACKEND` | `none` (off), `fake` (test mode: no e-mail, signing simulated), `docusign` |
| `DATAVIV_DOCUSIGN_INTEGRATION_KEY` | The DocuSign app's integration key |
| `DATAVIV_DOCUSIGN_USER_ID` | The DocuSign user the app acts as (a GUID, "User ID" on the Apps and Keys page) |
| `DATAVIV_DOCUSIGN_PRIVATE_KEY_PATH` | File with that app's RSA private key (or `DATAVIV_DOCUSIGN_PRIVATE_KEY` with the PEM text) |
| `DATAVIV_DOCUSIGN_AUTH_SERVER` | `account-d.docusign.com` (developer sandbox, default) or `account.docusign.com` (production) |
| `DATAVIV_DOCUSIGN_ACCOUNT_ID` | Optional; the user's default account otherwise |
| `DATAVIV_DOCUSIGN_WEBHOOK_URL` | Optional public `https://…/v1/esign/docusign/connect` for live status |
| `DATAVIV_DOCUSIGN_CONNECT_SECRET` | Connect HMAC key; callbacks are refused without it outside dev |

## Connecting DocuSign (developer sandbox first)

1. Create a free **developer** account at developers.docusign.com. This is a sandbox, separate from a live DocuSign account, and nothing sent from it is legally binding.
2. Open **Settings → Apps and Keys** and copy the **User ID**. Then choose **Add App and Integration Key** and copy the integration key.
3. In the app, under **Service Integration**, choose **Generate RSA**. Save the private key to a file outside the repository, for example `~/.dataviv/docusign_private.key`, readable only by you (`chmod 600`).
4. Under **Additional settings → Redirect URIs**, add `https://developers.docusign.com/platform/auth/consent`.
5. In `backend/.env`, set `DATAVIV_ESIGN_BACKEND=docusign`, the integration key, the user ID and `DATAVIV_DOCUSIGN_PRIVATE_KEY_PATH`. Restart the API.
6. Open any approved draft, then the **Signature** tab. The first time, it shows a consent link. Open it once, sign in as that DocuSign user, and accept. From then on the app signs in by itself.
7. Send to yourself first. For production, complete DocuSign's go-live review for the integration key. Then switch `DATAVIV_DOCUSIGN_AUTH_SERVER` to `account.docusign.com` and use the production user ID and keys.

In Docker, mount the key file into the `api` container and point `DATAVIV_DOCUSIGN_PRIVATE_KEY_PATH` at it. Alternatively, put the PEM in `DATAVIV_DOCUSIGN_PRIVATE_KEY` with its line breaks written as `\n`.

## Tests (`tests/test_esign.py`)

- Two signers in order; the signed copy and certificate land in the Vault, indexed and searchable; activity is recorded.
- Only a lead or partner sends, and only an approved draft. The IT Act exclusions must be confirmed. Duplicate or invalid e-mail addresses are refused. Only one envelope can be open per draft.
- Decline and void end an envelope, which can then be sent again. Members cannot void.
- Viewers see status. Other firms and walled users get 404.
- The background sweep, with no firm context, finds signatures made outside the app.
- Connect callbacks need a valid HMAC; unknown envelopes are acknowledged and ignored.
- The DocuSign client against a mock DocuSign server:
  - the RS256 JWT-grant assertion (issuer, subject, audience, scope, one-hour expiry) and token reuse;
  - account selection;
  - the envelope payload: the approved Word file, white anchors, routing order;
  - status mapping, download of the signed copy and void.
- The DocuSign client gives clear setup messages: a missing consent produces a consent link, and missing settings or a bad key are named.

Not covered: a live DocuSign sandbox call. It needs your developer keys; once they are in `backend/.env`, `GET /v1/esign/status` confirms the connection.
