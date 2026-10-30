import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import {
  ApiError,
  api,
  errorText,
  when,
  type DiffResult,
  type DraftDetail,
  type DraftHead,
  type DraftKind2,
  type DraftStatus,
  type Identity,
  type MemberDetail,
  type Suggestion,
} from "../api";
import { useInterval, useLoad } from "../hooks";
import type { Selection } from "./Inspector";
import { LiveEditor, type LiveEvent, type LiveHandle, type LiveSelection } from "./LiveEditor";
import { SignaturePanel } from "./SignaturePanel";
import { Empty, ErrorNote } from "./ui";

const KINDS: { id: DraftKind2; name: string }[] = [
  { id: "notice", name: "Legal notice" },
  { id: "reply", name: "Reply to notice" },
  { id: "email", name: "Client email" },
  { id: "memo", name: "Internal memo" },
  { id: "other", name: "Other" },
];
const STATUS: Record<DraftStatus, string> = {
  draft: "Draft",
  in_review: "Awaiting approval",
  changes_requested: "Changes requested",
  approved: "Approved",
};
const ACTIONS: Record<string, string> = {
  "draft.create": "created the draft",
  "draft.save": "saved a version",
  "draft.restore": "restored an earlier version",
  "draft.update": "renamed or re-typed it",
  "draft.review_request": "asked for approval",
  "draft.approve": "approved it",
  "draft.changes_requested": "requested changes",
  "draft.review_withdrawn": "withdrew the approval request",
  "draft.comment": "commented",
  "draft.comment_resolved": "resolved a comment",
  "draft.export": "exported it to Word",
  "draft.suggestion": "asked the AI for a suggestion",
  "draft.suggestion_accepted": "accepted an AI suggestion",
  "draft.suggestion_rejected": "rejected an AI suggestion",
  "draft.suggestion_stale": "tried an AI suggestion whose text had changed",
  "ai.call": "used the AI",
  "draft.signature_sent": "sent it for signature",
  "draft.signed": "received the copy signed by everyone",
  "draft.signature_declined": "was told a signer declined",
  "draft.signature_voided": "voided the signature request",
};

type Filter = "all" | "mine" | "in_review" | "approved";

export function DraftsTab(props: {
  matterId: string;
  who: Identity;
  openDraftId: string | null;
  onInspect: (s: Selection) => void;
}) {
  const [draftId, setDraftId] = useState<string | null>(props.openDraftId);
  const [filter, setFilter] = useState<Filter>("all");
  const [rev, setRev] = useState(0);
  const list = useLoad(() => api.drafts(props.matterId), [props.matterId, rev]);
  const [creating, setCreating] = useState(false);
  const [inlineErr, alertInline] = useState<string | null>(null);
  // null = no choice made yet: fold the list automatically when there is not room for page + side panel.
  const [listHidden, setListHidden] = useState<boolean | null>(() => {
    try {
      const v = localStorage.getItem("drafts.listHidden");
      return v === null ? null : v === "1";
    } catch {
      return null;
    }
  });
  const box = useRef<HTMLElement>(null);
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([e]) => setNarrow(e.contentRect.width < 1060));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  // The list can only fold away while a draft is open; otherwise there is nothing to show.
  const folded = !!draftId && (listHidden ?? narrow);

  function toggleList(hide: boolean) {
    setListHidden(hide);
    try {
      localStorage.setItem("drafts.listHidden", hide ? "1" : "0");
    } catch {
      /* storage unavailable: keep the choice for this visit only */
    }
  }

  useEffect(() => {
    if (props.openDraftId) setDraftId(props.openDraftId);
  }, [props.openDraftId]);

  const rows = (list.data ?? []).filter((d) =>
    filter === "all"
      ? true
      : filter === "mine"
        ? d.status === "in_review" && d.reviewer?.id === props.who.user_id
        : d.status === filter,
  );
  const mine = (list.data ?? []).filter((d) => d.status === "in_review" && d.reviewer?.id === props.who.user_id).length;

  async function newDraft() {
    setCreating(true);
    try {
      const d = await api.createDraft(props.matterId, { title: "Untitled draft", kind: "other", content: "" });
      setRev((r) => r + 1);
      setDraftId(d.id);
    } catch (e) {
      alertInline(errorText(e));
    } finally {
      setCreating(false);
    }
  }

  return (
    <section ref={box} className={`drafts ${folded ? "is-folded" : ""}`}>
      {folded ? (
        <aside className="threads threads-folded" aria-label="Drafts">
          <button className="threads-unfold" onClick={() => toggleList(false)} title="Show the list of drafts">
            <span aria-hidden="true">›</span>
            <span className="threads-unfold-label">All drafts{list.data ? ` (${list.data.length})` : ""}</span>
          </button>
        </aside>
      ) : (
      <aside className="threads" aria-label="Drafts">
        <div className="threads-top">
          <button className="btn btn-primary threads-new" onClick={newDraft} disabled={creating}>
            New draft
          </button>
          {draftId && (
            <button className="btn btn-small btn-quiet" onClick={() => toggleList(true)} title="Hide the list to give the draft more room">
              ‹ Hide
            </button>
          )}
        </div>
        {inlineErr && <ErrorNote>{inlineErr}</ErrorNote>}
        <div className="draft-filters" role="radiogroup" aria-label="Show">
          {(
            [
              ["all", "All"],
              ["mine", mine ? `To approve (${mine})` : "To approve"],
              ["in_review", "In review"],
              ["approved", "Approved"],
            ] as [Filter, string][]
          ).map(([id, name]) => (
            <button
              key={id}
              role="radio"
              aria-checked={filter === id}
              className={`pick ${filter === id ? "is-on" : ""}`}
              onClick={() => setFilter(id)}
            >
              {name}
            </button>
          ))}
        </div>
        {list.error && <ErrorNote>{list.error}</ErrorNote>}
        {list.data && rows.length === 0 && (
          <p className="muted threads-empty">
            {filter === "all" ? "No drafts yet. Start one here, or use Edit as draft on an Assistant answer." : "Nothing here."}
          </p>
        )}
        <ul>
          {rows.map((d) => (
            <li key={d.id}>
              <button className={`thread ${draftId === d.id ? "is-on" : ""}`} onClick={() => setDraftId(d.id)}>
                <span className="thread-title">{d.title}</span>
                <span className="thread-meta">
                  <span className={`dot dot-${d.status}`} aria-hidden="true" /> {STATUS[d.status]} · v{d.current_version_no}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </aside>
      )}
      {draftId ? (
        <Editor
          key={draftId}
          draftId={draftId}
          matterId={props.matterId}
          who={props.who}
          onChanged={() => setRev((r) => r + 1)}
          onInspect={props.onInspect}
        />
      ) : (
        <Empty title="Choose a draft">
          Drafts are shared with everyone on this matter. Each save is kept as a version, and a lead or partner on the
          matter approves the final version.
        </Empty>
      )}
    </section>
  );
}

type SideTab = "ai" | "versions" | "comments" | "approval" | "signature" | "activity" | "sources";

function Editor(props: {
  draftId: string;
  matterId: string;
  who: Identity;
  onChanged: () => void;
  onInspect: (s: Selection) => void;
}) {
  const [d, setD] = useState<DraftDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [base, setBase] = useState(1);
  const [note, setNote] = useState("");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState<string | null>(null);
  const [head, setHead] = useState<DraftHead | null>(null);
  const [diffView, setDiffView] = useState<DiffResult | null>(null);
  const [side, setSide] = useState<SideTab>("ai");
  const [saved, setSaved] = useState<string | null>(null);
  const area = useRef<HTMLTextAreaElement>(null);
  const liveRef = useRef<LiveHandle | null>(null);
  const [liveEdited, setLiveEdited] = useState(false);
  const [liveSel, setLiveSel] = useState<LiveSelection | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const isLive = !!d?.live.available;
  const dirty = !!d && (isLive ? d.live.unsaved_changes || liveEdited : text !== d.content);

  const load = useCallback(async () => {
    try {
      const fresh = await api.draft(props.draftId);
      setD(fresh);
      setText(fresh.content);
      setBase(fresh.current_version_no);
      setTitle(fresh.title);
      setConflict(null);
      setErr(null);
      setLiveEdited(false);
    } catch (e) {
      setErr(errorText(e));
    }
  }, [props.draftId]);

  // Someone else saved, commented, asked the AI or decided: refresh (debounced) and say who.
  const refreshTimer = useRef<number | null>(null);
  const onLive = useCallback(
    (e: LiveEvent) => {
      if (e.type === "hello") return;
      if (e.type === "changed" && e.by !== props.who.user_id) {
        const what: Record<string, string> = {
          version: "saved a version",
          suggestion: "updated an AI suggestion",
          comments: "updated the comments",
          review: "changed the approval status",
          signature: "updated the signature status",
          details: "changed the title or type",
        };
        const name = d?.present.find((p) => p.user.id === e.by)?.user.name ?? "A colleague";
        setToast(`${name} ${what[e.what] ?? "made a change"}`);
        window.setTimeout(() => setToast(null), 4000);
      }
      if (refreshTimer.current) window.clearTimeout(refreshTimer.current);
      refreshTimer.current = window.setTimeout(() => {
        void load();
        props.onChanged();
      }, 250);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [load, d?.present, props.who.user_id],
  );

  function currentSelection(): string | null {
    if (isLive) return liveRef.current?.selection()?.text.slice(0, 2000) ?? null;
    const el = area.current;
    return el && el.selectionEnd > el.selectionStart ? el.value.slice(el.selectionStart, el.selectionEnd).slice(0, 2000) : null;
  }

  async function saveLive() {
    if (!d) return;
    setBusy(true);
    setErr(null);
    try {
      const v = await api.liveSave(d.id, note.trim() || undefined);
      setNote("");
      setSaved(`Saved as version ${v.version_no}${v.contributors.length > 1 ? ` (${v.contributors.map((c) => c.name).join(", ")})` : ""}`);
      setTimeout(() => setSaved(null), 4000);
      await load();
      props.onChanged();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void load();
  }, [load]);

  // Presence + change detection every 5 s. If nothing is unsaved, newer work is loaded automatically.
  const beat = async () => {
    if (!d) return;
    try {
      const h = await api.heartbeat(props.draftId, dirty);
      setHead(h);
      const moved = h.current_version_no !== d.current_version_no || h.status !== d.status;
      if (moved && !dirty) {
        await load();
        props.onChanged();
      }
    } catch {
      /* the request log shows the failure; keep editing */
    }
  };
  useInterval(beat, 5000, !!d);
  useEffect(() => {
    if (d) void beat();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d?.id]);

  async function run(action: () => Promise<DraftDetail | unknown>, after?: () => void) {
    setBusy(true);
    setErr(null);
    try {
      const out = await action();
      if (out && typeof out === "object" && "versions" in out) {
        const fresh = out as DraftDetail;
        setD(fresh);
        if (!dirty) setText(fresh.content);
        setBase(fresh.current_version_no);
      } else {
        await load();
      }
      after?.();
      props.onChanged();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  async function save(force = false) {
    if (!d) return;
    setBusy(true);
    setErr(null);
    try {
      const v = await api.saveVersion(d.id, { content: text, base_version_no: base, note: note.trim() || undefined, force });
      setNote("");
      setSaved(`Saved as version ${v.version_no}`);
      setTimeout(() => setSaved(null), 3000);
      await load();
      props.onChanged();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && !e.detail.includes("waiting for approval")) setConflict(e.detail);
      else setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
      e.preventDefault();
      if (dirty && !busy) void save();
    }
  }

  async function compare(from: number, to: number) {
    try {
      setDiffView(await api.diff(props.draftId, from, to));
    } catch (e) {
      setErr(errorText(e));
    }
  }

  if (err && !d) return <ErrorNote>{err}</ErrorNote>;
  if (!d) return <p className="muted pad">Loading…</p>;

  const pending = d.reviews.find((r) => r.status === "pending") ?? null;
  const iApprove = pending?.reviewer.id === props.who.user_id;
  const others = head?.present ?? d.present.filter((x) => x.user.id !== props.who.user_id);
  const newer = !isLive && head && head.current_version_no > base ? head : null;
  const openComments = d.comments.filter((c) => !c.resolved_at).length;
  const pendingSuggestions = d.suggestions.filter((s) => s.status === "pending");
  const locked = d.status === "in_review";
  const mayEdit = d.my_matter_role !== "viewer" && ["lead", "partner", "member"].includes(d.my_matter_role);

  return (
    <div className="editor">
      <div className="editor-head">
        <input
          className="editor-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          onBlur={() => title.trim() && title !== d.title && run(() => api.updateDraft(d.id, { title }))}
          disabled={!d.can_edit}
          aria-label="Draft title"
        />
        <div className="editor-meta">
          <span className={`chip chip-d-${d.status}`}>{STATUS[d.status]}</span>
          <select
            value={d.kind}
            onChange={(e) => run(() => api.updateDraft(d.id, { kind: e.target.value as DraftKind2 }))}
            disabled={!d.can_edit}
            aria-label="Type of draft"
          >
            {KINDS.map((k) => (
              <option key={k.id} value={k.id}>
                {k.name}
              </option>
            ))}
          </select>
          <span className="muted">
            v{d.current_version_no} · {d.updated_by.name}, {when(d.updated_at)}
          </span>
          {d.approved_version_no && d.approved_by && (
            <span className="approved-note">
              v{d.approved_version_no} approved by {d.approved_by.name}
              {d.approved_at ? `, ${when(d.approved_at)}` : ""}
            </span>
          )}
          <span className="toolbar-gap" />
          {others.length > 0 && (
            <span className="present" title="Also viewing this draft now">
              {others.map((x) => (
                <span key={x.user.id} className={`who-chip ${x.editing ? "is-editing" : ""}`}>
                  {x.user.name.split(" ")[0]}
                  {x.editing ? " · editing" : ""}
                </span>
              ))}
            </span>
          )}
        </div>
      </div>

      <StatusBanner d={d} pending={pending} iApprove={iApprove} who={props.who} busy={busy} run={run} matterId={props.matterId} />

      {newer && !conflict && (
        <p className="note">
          {newer.updated_by.name} saved version {newer.current_version_no}.{" "}
          {dirty ? (
            <>
              You have unsaved changes.{" "}
              <button className="link" onClick={() => compare(base, newer.current_version_no)}>
                See what they changed
              </button>
            </>
          ) : (
            <button className="link" onClick={load}>
              Load it
            </button>
          )}
        </p>
      )}
      {conflict && (
        <div className="note note-warn" role="alert">
          <p>{conflict}</p>
          <div className="row">
            <button className="btn btn-small" onClick={() => compare(base, head?.current_version_no ?? base + 1)}>
              See their changes
            </button>
            <button className="btn btn-small btn-primary" onClick={() => save(true)} disabled={busy}>
              Save mine on top
            </button>
            <button className="btn btn-small btn-quiet" onClick={load}>
              Discard mine and load theirs
            </button>
          </div>
        </div>
      )}
      {err && <ErrorNote>{err}</ErrorNote>}

      <div className="editor-body">
        <div className="page-wrap">
          {diffView ? (
            <div className="page page-diff">
              <div className="diff-head">
                <strong>
                  Changes from v{diffView.from_version} to v{diffView.to_version}
                </strong>
                <span className="muted">
                  {diffView.inserted_words} words added · {diffView.deleted_words} removed
                </span>
                <span className="toolbar-gap" />
                <button className="btn btn-small" onClick={() => setDiffView(null)}>
                  Back to editing
                </button>
              </div>
              <div className="diff-text">
                {diffView.ops.map((o, i) =>
                  o.op === "equal" ? (
                    <span key={i}>{o.text}</span>
                  ) : o.op === "insert" ? (
                    <ins key={i}>{o.text}</ins>
                  ) : (
                    <del key={i}>{o.text}</del>
                  ),
                )}
              </div>
            </div>
          ) : isLive ? (
            <LiveEditor
              draftId={d.id}
              userName={props.who.name}
              canEdit={mayEdit}
              locked={locked}
              highlights={pendingSuggestions.map((s) => s.quote)}
              handleRef={liveRef}
              onEvent={onLive}
              onLocalEdit={() => setLiveEdited(true)}
              onSelection={setLiveSel}
            />
          ) : (
            <textarea
              ref={area}
              className="page"
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={onKey}
              readOnly={!d.can_edit}
              spellCheck
              aria-label="Draft text"
              placeholder={d.can_edit ? "Start writing, or paste text here." : ""}
            />
          )}
          {toast && <p className="note live-toast">{toast}</p>}
          {isLive && d.can_edit && !diffView && (
            <div className="save-bar">
              <span className={dirty ? "unsaved" : "muted"}>
                {saved ??
                  (dirty
                    ? `Unsaved changes since v${d.current_version_no}${
                        d.live.contributors.length ? ` by ${d.live.contributors.map((c) => c.name.split(" ")[0]).join(", ")}` : ""
                      }`
                    : `Same as v${d.current_version_no}`)}
              </span>
              <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="What changed? (optional)" aria-label="Version note" />
              <button className="btn btn-primary" onClick={saveLive} disabled={busy} title="Saves everyone's changes as one version">
                {busy ? "Saving…" : `Save as v${d.current_version_no + 1}`}
              </button>
            </div>
          )}
          {!isLive && d.can_edit && !diffView && (
            <div className="save-bar">
              <span className={dirty ? "unsaved" : "muted"}>
                {saved ?? (dirty ? `Unsaved changes (editing from v${base})` : "All changes saved")}
              </span>
              <input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="What changed? (optional)"
                aria-label="Version note"
              />
              <button className="btn btn-primary" onClick={() => save()} disabled={!dirty || busy} title="Ctrl+S">
                {busy ? "Saving…" : `Save as v${d.current_version_no + 1}`}
              </button>
            </div>
          )}
          <div className="row editor-foot">
            <button className="btn btn-small" onClick={() => api.exportDraft(d.id).catch((e) => setErr(errorText(e)))}>
              Export v{d.current_version_no} to Word
            </button>
            {d.approved_version_no && d.approved_version_no !== d.current_version_no && (
              <button className="btn btn-small" onClick={() => api.exportDraft(d.id, d.approved_version_no!).catch((e) => setErr(errorText(e)))}>
                Export approved v{d.approved_version_no}
              </button>
            )}
          </div>
        </div>

        <aside className="draft-side">
          <nav className="side-tabs" role="tablist">
            {(
              [
                ["ai", `AI${pendingSuggestions.length ? ` (${pendingSuggestions.length})` : ""}`],
                ["versions", `Versions (${d.versions.length})`],
                ["comments", `Comments${openComments ? ` (${openComments})` : ""}`],
                ["approval", "Approval"],
                ...(d.approved_version_no || d.signatures.length
                  ? [["signature", d.signatures.some((s) => s.status === "completed") ? "Signed ✓" : "Signature"]]
                  : []),
                ["activity", "Activity"],
                ...(d.sources.length ? [["sources", `Sources (${d.sources.length})`]] : []),
              ] as [SideTab, string][]
            ).map(([id, name]) => (
              <button key={id} role="tab" aria-selected={side === id} className={`tab ${side === id ? "is-on" : ""}`} onClick={() => setSide(id)}>
                {name}
              </button>
            ))}
          </nav>
          {side === "ai" && (
            <AiPanel
              d={d}
              live={isLive}
              selection={isLive ? liveSel : null}
              onInspect={props.onInspect}
              onDone={() => {
                void load();
                props.onChanged();
              }}
            />
          )}
          {side === "versions" && (
            <ol className="ver-list">
              {d.versions.map((v) => (
                <li key={v.version_no} className={v.version_no === d.approved_version_no ? "is-approved" : undefined}>
                  <div className="ver-head">
                    <strong>v{v.version_no}</strong>
                    {v.version_no === d.current_version_no && <span className="tag">current</span>}
                    {v.version_no === d.approved_version_no && <span className="tag tag-ok">approved</span>}
                    <span className="muted">
                      {v.created_by.name}, {when(v.created_at)}
                    </span>
                  </div>
                  {v.note && <p className="ver-note">{v.note}</p>}
                  {v.contributors.length > 1 && (
                    <p className="ver-note muted">With changes by {v.contributors.map((c) => c.name).join(", ")}</p>
                  )}
                  <div className="row">
                    {v.version_no > 1 && (
                      <button className="link" onClick={() => compare(v.version_no - 1, v.version_no)}>
                        Changes in this version
                      </button>
                    )}
                    {v.version_no !== d.current_version_no && (
                      <>
                        <button className="link" onClick={() => compare(v.version_no, d.current_version_no)}>
                          Compare with current
                        </button>
                        {d.can_edit && (
                          <button
                            className="link"
                            disabled={dirty}
                            title={dirty ? "Save or discard your changes first" : undefined}
                            onClick={() => run(() => api.restoreVersion(d.id, v.version_no))}
                          >
                            Restore as new version
                          </button>
                        )}
                      </>
                    )}
                  </div>
                </li>
              ))}
            </ol>
          )}
          {side === "comments" && <Comments d={d} grab={currentSelection} run={run} />}
          {side === "approval" && (
            <ol className="ver-list">
              {d.reviews.length === 0 && <p className="muted">No approval requests yet.</p>}
              {d.reviews.map((r) => (
                <li key={r.id}>
                  <div className="ver-head">
                    <strong>v{r.version_no}</strong>
                    <span className={`chip chip-r-${r.status}`}>{r.status.replace("_", " ")}</span>
                  </div>
                  <p className="ver-note">
                    {r.requested_by.name} asked {r.reviewer.name}, {when(r.created_at)}
                    {r.message ? `: “${r.message}”` : ""}
                  </p>
                  {r.decision_note && <p className="ver-note">Note: {r.decision_note}</p>}
                </li>
              ))}
            </ol>
          )}
          {side === "signature" && (
            <SignaturePanel
              d={d}
              who={props.who}
              onInspect={props.onInspect}
              onDone={() => {
                void load();
                props.onChanged();
              }}
            />
          )}
          {side === "activity" && (
            <ol className="activity">
              {d.activity.map((a, i) => (
                <li key={i}>
                  <strong>{a.actor?.name ?? "Someone"}</strong> {ACTIONS[a.action] ?? a.action}
                  {typeof a.detail.version_no === "number" ? ` (v${a.detail.version_no})` : ""}
                  <span className="muted"> · {when(a.at)}</span>
                </li>
              ))}
            </ol>
          )}
          {side === "sources" && (
            <ol className="ans-sources">
              {d.sources.map((s) => (
                <li key={s.n} value={s.n}>
                  <button
                    className="src"
                    onClick={() => props.onInspect({ kind: "chunk", docId: s.document_id, chunkId: s.chunk_id, terms: [] })}
                  >
                    <span className="src-title">
                      {s.document_title}, {s.page_from === s.page_to ? `p. ${s.page_from}` : `pp. ${s.page_from}–${s.page_to}`}
                      {s.section_path ? ` · ${s.section_path}` : ""}
                    </span>
                  </button>
                </li>
              ))}
            </ol>
          )}
        </aside>
      </div>
    </div>
  );
}

function StatusBanner(props: {
  d: DraftDetail;
  pending: DraftDetail["reviews"][number] | null;
  iApprove: boolean;
  who: Identity;
  busy: boolean;
  matterId: string;
  run: (a: () => Promise<unknown>, after?: () => void) => Promise<void>;
}) {
  const { d, pending, iApprove, run } = props;
  const [asking, setAsking] = useState(false);
  const [decisionNote, setDecisionNote] = useState("");
  const last = d.reviews[0];

  if (pending) {
    const mineToWithdraw = pending.requested_by.id === props.who.user_id || iApprove;
    return (
      <div className={`banner ${iApprove ? "banner-act" : "banner-wait"}`}>
        <p>
          {iApprove ? (
            <>
              <strong>{pending.requested_by.name}</strong> asks you to approve version {pending.version_no}
              {pending.message ? `: “${pending.message}”` : "."}
            </>
          ) : (
            <>
              Waiting for <strong>{pending.reviewer.name}</strong> to approve version {pending.version_no}. Editing is locked
              until they decide or the request is withdrawn.
            </>
          )}
        </p>
        {iApprove && (
          <div className="row">
            <input
              className="grow"
              value={decisionNote}
              onChange={(e) => setDecisionNote(e.target.value)}
              placeholder="Note to the author (needed when requesting changes)"
              aria-label="Decision note"
            />
            <button className="btn btn-primary" disabled={props.busy} onClick={() => run(() => api.decide(d.id, "approve", decisionNote || undefined))}>
              Approve v{pending.version_no}
            </button>
            <button
              className="btn"
              disabled={props.busy || !decisionNote.trim()}
              onClick={() => run(() => api.decide(d.id, "request_changes", decisionNote))}
            >
              Request changes
            </button>
          </div>
        )}
        {mineToWithdraw && !iApprove && (
          <button className="btn btn-small btn-quiet" onClick={() => run(() => api.withdraw(d.id))}>
            Withdraw request
          </button>
        )}
      </div>
    );
  }

  const canAsk = d.can_edit && !(d.status === "approved" && d.approved_version_no === d.current_version_no);
  return (
    <>
      {d.status === "changes_requested" && last?.decision_note && (
        <div className="banner banner-warn">
          <p>
            <strong>{last.reviewer.name}</strong> requested changes to v{last.version_no}: “{last.decision_note}”
          </p>
        </div>
      )}
      {d.status === "approved" && d.approved_version_no === d.current_version_no && (
        <div className="banner banner-ok">
          <p>
            Version {d.approved_version_no} is approved. Saving changes starts version {d.current_version_no + 1}; the approved
            version stays on record.
          </p>
        </div>
      )}
      {canAsk &&
        (asking ? (
          <AskApproval d={d} matterId={props.matterId} who={props.who} busy={props.busy} run={run} onClose={() => setAsking(false)} />
        ) : (
          <div className="row">
            <button className="btn btn-small" onClick={() => setAsking(true)}>
              Ask for approval of v{d.current_version_no}
            </button>
          </div>
        ))}
    </>
  );
}

function AskApproval(props: {
  d: DraftDetail;
  matterId: string;
  who: Identity;
  busy: boolean;
  run: (a: () => Promise<unknown>, after?: () => void) => Promise<void>;
  onClose: () => void;
}) {
  const people = useLoad(() => api.people(props.matterId), [props.matterId]);
  const approvers = (people.data?.members ?? []).filter(
    (m: MemberDetail) => (m.matter_role === "lead" || m.matter_role === "partner") && m.user_id !== props.who.user_id,
  );
  const [reviewer, setReviewer] = useState("");
  const [message, setMessage] = useState("");
  return (
    <div className="banner">
      <div className="row">
        <select value={reviewer} onChange={(e) => setReviewer(e.target.value)} aria-label="Approver">
          <option value="">Choose the approver</option>
          {approvers.map((m) => (
            <option key={m.user_id} value={m.user_id}>
              {m.name} ({m.matter_role})
            </option>
          ))}
        </select>
        <input className="grow" value={message} onChange={(e) => setMessage(e.target.value)} placeholder="Message (optional)" />
        <button
          className="btn btn-primary"
          disabled={!reviewer || props.busy}
          onClick={() => props.run(() => api.requestReview(props.d.id, reviewer, message || undefined), props.onClose)}
        >
          Send for approval
        </button>
        <button className="btn btn-quiet" onClick={props.onClose}>
          Cancel
        </button>
      </div>
      {people.data && approvers.length === 0 && (
        <p className="muted">Nobody else on this matter is a lead or partner. Add one on the People tab.</p>
      )}
    </div>
  );
}

function Comments(props: {
  d: DraftDetail;
  grab: () => string | null;
  run: (a: () => Promise<unknown>, after?: () => void) => Promise<void>;
}) {
  const [body, setBody] = useState("");
  const [quote, setQuote] = useState<string | null>(null);
  const grab = () => {
    const q = props.grab();
    if (q) setQuote(q);
  };
  return (
    <div className="comments">
      {props.d.comments.length === 0 && <p className="muted">No comments yet. Anyone on the matter can comment.</p>}
      <ol>
        {props.d.comments.map((c) => (
          <li key={c.id} className={c.resolved_at ? "is-resolved" : undefined}>
            {c.quote && <blockquote>{c.quote}</blockquote>}
            <p>{c.body}</p>
            <div className="row">
              <span className="muted">
                {c.author.name} on v{c.version_no}, {when(c.created_at)}
              </span>
              <span className="toolbar-gap" />
              {c.resolved_at ? (
                <span className="muted">Resolved by {c.resolved_by?.name}</span>
              ) : (
                <button className="link" onClick={() => props.run(() => api.resolveComment(props.d.id, c.id))}>
                  Resolve
                </button>
              )}
            </div>
          </li>
        ))}
      </ol>
      <div className="comment-new">
        {quote ? (
          <blockquote>
            {quote}{" "}
            <button className="link" onClick={() => setQuote(null)}>
              remove
            </button>
          </blockquote>
        ) : (
          <button className="link" onMouseDown={grab}>
            Quote the text selected in the draft
          </button>
        )}
        <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={3} placeholder="Add a comment" aria-label="Comment" />
        <button
          className="btn btn-small btn-primary"
          disabled={!body.trim()}
          onClick={() =>
            props.run(
              () => api.comment(props.d.id, body.trim(), quote),
              () => {
                setBody("");
                setQuote(null);
              },
            )
          }
        >
          Comment
        </button>
      </div>
    </div>
  );
}

const SUGGESTION_STATUS: Record<Suggestion["status"], string> = {
  pending: "Waiting for a decision",
  accepted: "Accepted",
  rejected: "Rejected",
  stale: "Text changed before it was accepted",
};

/** Ask the AI to change the selected text (or the whole draft); everyone sees its suggestion as a redline. */
function AiPanel(props: {
  d: DraftDetail;
  live: boolean;
  selection: LiveSelection | null;
  onInspect: (s: Selection) => void;
  onDone: () => void;
}) {
  const { d } = props;
  const [instruction, setInstruction] = useState("");
  const [useDocs, setUseDocs] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [showOld, setShowOld] = useState(false);
  const canAsk = d.can_edit && props.live;
  const pending = d.suggestions.filter((s) => s.status === "pending");
  const decided = d.suggestions.filter((s) => s.status !== "pending");
  const sel = props.selection && props.selection.text.trim() ? props.selection : null;

  async function ask() {
    setBusy("ask");
    setErr(null);
    try {
      await api.suggest(d.id, { instruction: instruction.trim(), selection: sel, use_documents: useDocs });
      setInstruction("");
      props.onDone();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  async function decide(s: Suggestion, accept: boolean) {
    setBusy(s.id);
    setErr(null);
    try {
      const out = accept ? await api.acceptSuggestion(d.id, s.id) : await api.rejectSuggestion(d.id, s.id);
      if (out.status === "stale") setErr("That text was changed after the suggestion was made, so it was not applied. Ask again.");
      props.onDone();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="ai-panel">
      {canAsk ? (
        <div className="ai-ask">
          <p className="muted">
            {sel ? (
              <>
                Working on the selected text ({sel.text.length} characters). <em>“{sel.text.slice(0, 80)}{sel.text.length > 80 ? "…" : ""}”</em>
              </>
            ) : (
              "Nothing selected: the AI will work on the whole draft. Select a clause in the draft to change just that."
            )}
          </p>
          <textarea
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            rows={3}
            placeholder="e.g. Tighten this clause and cite the SPA · Make it more formal · Add a 15-day cure period"
            aria-label="Instruction for the AI"
            onKeyDown={(e) => {
              if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && instruction.trim().length >= 3 && !busy) void ask();
            }}
          />
          <div className="row">
            <label className="check">
              <input type="checkbox" checked={useDocs} onChange={(e) => setUseDocs(e.target.checked)} /> Use this matter's documents
            </label>
            <span className="toolbar-gap" />
            <button className="btn btn-primary btn-small" disabled={instruction.trim().length < 3 || !!busy} onClick={ask}>
              {busy === "ask" ? "Asking the AI…" : "Suggest a change"}
            </button>
          </div>
          <p className="muted small">Suggestions appear for everyone on the draft. Nothing changes until an editor accepts one.</p>
        </div>
      ) : (
        <p className="muted">
          {d.status === "in_review"
            ? "The draft is waiting for approval: AI suggestions are paused."
            : !props.live
              ? "AI suggestions need live editing, which is not available on this server."
              : "Viewers can see suggestions but not ask for or decide on them."}
        </p>
      )}
      {err && <ErrorNote>{err}</ErrorNote>}

      {pending.length === 0 && <p className="muted">No suggestions waiting.</p>}
      <ol className="suggestions">
        {pending.map((s) => (
          <SuggestionCard key={s.id} s={s} canDecide={d.can_edit} busy={busy === s.id} onDecide={decide} onInspect={props.onInspect} />
        ))}
      </ol>
      {decided.length > 0 && (
        <>
          <button className="link" onClick={() => setShowOld(!showOld)}>
            {showOld ? "Hide" : "Show"} earlier suggestions ({decided.length})
          </button>
          {showOld && (
            <ol className="suggestions is-old">
              {decided.map((s) => (
                <SuggestionCard key={s.id} s={s} canDecide={false} busy={false} onDecide={decide} onInspect={props.onInspect} />
              ))}
            </ol>
          )}
        </>
      )}
    </div>
  );
}

function SuggestionCard(props: {
  s: Suggestion;
  canDecide: boolean;
  busy: boolean;
  onDecide: (s: Suggestion, accept: boolean) => void;
  onInspect: (s: Selection) => void;
}) {
  const { s } = props;
  return (
    <li className={`suggestion sug-${s.status}`}>
      <div className="ver-head">
        <strong>{s.requested_by.name}</strong>
        <span className="muted">{when(s.created_at)}</span>
        <span className="toolbar-gap" />
        <span className={`chip chip-sug-${s.status}`}>{SUGGESTION_STATUS[s.status]}</span>
      </div>
      <p className="sug-instruction">“{s.instruction}”{s.scope === "draft" ? " (whole draft)" : ""}</p>
      <div className="diff-text sug-diff">
        {s.diff.map((o, i) =>
          o.op === "equal" ? <span key={i}>{o.text}</span> : o.op === "insert" ? <ins key={i}>{o.text}</ins> : <del key={i}>{o.text}</del>,
        )}
      </div>
      {s.explanation && <p className="ver-note">{s.explanation}</p>}
      {s.warnings.map((w) => (
        <p key={w} className="sug-warning">
          {w}
        </p>
      ))}
      {s.sources.length > 0 && (
        <ul className="sug-sources">
          {s.sources.map((src) => (
            <li key={src.chunk_id}>
              <button
                className="link"
                onClick={() => props.onInspect({ kind: "chunk", docId: src.document_id, chunkId: src.chunk_id, terms: [] })}
              >
                {src.document_title}
                {src.section_path ? ` · ${src.section_path}` : ""}, p. {src.page_from}
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="row">
        <span className="muted small">
          {s.model} · ${s.cost_usd.toFixed(5)}
          {s.decided_by ? ` · ${s.status} by ${s.decided_by.name}` : ""}
        </span>
        <span className="toolbar-gap" />
        {props.canDecide && s.status === "pending" && (
          <>
            <button className="btn btn-small" disabled={props.busy} onClick={() => props.onDecide(s, false)}>
              Reject
            </button>
            <button className="btn btn-small btn-primary" disabled={props.busy} onClick={() => props.onDecide(s, true)}>
              {props.busy ? "Applying…" : "Accept"}
            </button>
          </>
        )}
      </div>
    </li>
  );
}
