# Step 2c — Live co-editing of drafts, with AI suggestions

**Implements:** DRF-08..11 · **Decision:** ADR-020 (builds on ADR-014) · **Status:** done, 16 October 2026. 140 tests on Linux (11 for live drafts); ruff and mypy clean. The live end-to-end run with Groq is in `logs/26_live_e2e.log`.

Several people in a firm now edit the same draft at the same moment, and each sees the others' typing and cursors as it happens. Anyone can ask the AI Assistant to change a selected clause, or the whole draft, in plain words. The AI's change appears to everyone as a redline. It reaches the draft only when an editor accepts it. When the text is final, it goes to the matter lead or a partner for approval, exactly as before (DRF-04). The approved version is exactly what everyone saw.

## How it works

```
 browser (CodeMirror + Yjs) ─┐                       ┌─ draft_live (Yjs state, contributors)
 browser (CodeMirror + Yjs) ─┼─ WS /v1/drafts/{id}/live ─ room (pycrdt) ──┤
 browser (CodeMirror + Yjs) ─┘   sync · awareness · events  └─ "Save version" ─► draft_versions (+ contributors)
            ▲
            └── REST: suggestions (AI) · accept / reject · save · approval ─► events to everyone in the room
```

- **Working copy and versions.** Typing goes into a shared CRDT document (Yjs), which merges concurrent edits without locks or conflicts. *Save version* turns the working copy into the next immutable version and credits everyone who typed into it, or whose AI suggestion was accepted, since the last version. History, comparison, restore and Word export are unchanged.
- **Approval.** *Ask for approval* first saves any unsaved changes as *"Saved for approval"*. It then locks the working copy for everyone, and the server refuses edits even from a stale browser. The approver approves that exact version. Approving, requesting changes or withdrawing unlocks the draft.
- **AI suggestions.** An editor selects text (or nothing, meaning the whole draft, up to 12,000 characters) and writes an instruction. With *Use this matter's documents* on, the instruction and the selected text are searched in the matter with hybrid search (Step 1c). The best passages go to the model, which must cite the ones it uses. The suggestion stores the original text, the replacement, a word-level redline, a one-line reason, the cited passages, warnings, the model and the cost. Everyone sees it at once, and the passage it would change is highlighted in the editor. **Accept** applies it to the working copy. If someone has changed that text in the meantime, the suggestion becomes *stale* and nothing is applied. **Reject** records the decision.
- **Guards on AI output.** Any figure (amount, date, clause number) in the replacement that is neither in the original text nor in a cited passage is flagged. Citing a passage the model was not given is flagged. Clause numbers and list markers the model drops are put back. Draft and source text is treated as data, never instructions.

## Who may do what

| | viewer | member | lead / partner |
|---|:-:|:-:|:-:|
| See live typing, cursors and suggestions | ✓ | ✓ | ✓ |
| Type, ask the AI, accept or reject suggestions, save versions | | ✓ | ✓ |
| Approve | | | ✓ when chosen, never their own request |

The server enforces all of this. The token travels in the WebSocket sub-protocol, never in the URL. Matter access is checked on connect and every 15 seconds, so a removed or walled colleague is disconnected. A change from a viewer, or from anyone while the draft is locked, is not applied, and the socket closes (4403 / 4423) so the browser discards it. Other firms and admins get 4404. Cursor names come from the browser and are for display only: attribution comes from the authenticated socket.

## API

| Method & path | Purpose |
|---|---|
| `WS /v1/drafts/{id}/live` | y-websocket protocol; sub-protocols `["dataviv", <token>]`; extra message type 100 carries JSON events (`hello`, `status`, `access`, `changed`) |
| `GET /v1/drafts/{id}/live/text` | The working copy now, and whether it differs from the current version |
| `POST /v1/drafts/{id}/live/save` | `{note?}` → the next version, with contributors |
| `POST /v1/drafts/{id}/suggestions` | `{instruction, selection?: {text, start}, use_documents, model?}` → a pending suggestion |
| `POST /v1/drafts/{id}/suggestions/{sid}/accept` · `/reject` | Decide; accept returns `stale` if the text has changed |
| `GET /v1/drafts/{id}` | Gains `live` (available, unsaved changes, contributors) and `suggestions`; versions gain `contributors` |

Saving, restoring and approval through the existing endpoints keep the working copy in step and notify everyone in the room.

## Console

The Drafts tab uses a live editor (CodeMirror 6 + Yjs) with a connection status and people chips. Other people's cursors appear in their own colours. The **AI** side tab holds the prompt box, the suggestion cards (redline, reason, sources, warnings, cost, Accept / Reject) and earlier suggestions. Passages with pending suggestions are underlined in the text. *Save as vN* names everyone who contributed. Where the server cannot run live editing, the classic save-a-version editor is still there.

## Verified live (16 October, aman-desktop, Groq `openai/gpt-oss-120b`)

1. Asha's edit from the previous evening survived a full machine restart (it was persisted in `draft_live`).
2. Rohan typed at the top while Asha edited the end, at the same instant; both ended with identical text.
3. Rohan asked the AI to turn clause 3 into a formal termination warning with a 15-day cure period, citing the SPA. The suggestion took 10.7 s, cost $0.00038, cited *lotus-spa.txt · 12. TERMINATION*, and had no warnings. Asha was notified live, and the draft did not change until she accepted.
4. v2 was saved, crediting Asha and Rohan. Rohan sent it to Asha for approval, and his next keystroke was refused (4423). Asha approved v2, and editing unlocked.

## Deferred

| Item | Why / when |
|---|---|
| Several API processes | One process holds the rooms. Scaling out needs sticky routing per draft or a broker (Redis / Postgres NOTIFY) between processes (ADR-020). |
| Rich text (headings, tables) | Plain text keeps redlines and diffs exact; TipTap + Yjs can replace CodeMirror without changing the server. |
| Suggestions applied as tracked changes inside the text | Today the redline is in the side panel and the target is highlighted; inline redlines come with rich text. |
| Windows dev PC | pycrdt is compiled (Rust). If Smart App Control blocks it, the API still runs and drafts fall back to versioned saves. |
