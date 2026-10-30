import { useState, type FormEvent, type ReactNode } from "react";
import {
  api,
  errorText,
  label,
  when,
  type Clause,
  type Playbook,
  type PlaybookInput,
  type RunDetail,
  type RunStatus,
  type WorkflowInput,
  type WorkflowStep,
  type WorkflowSummary,
} from "../api";
import { useInterval, useLoad } from "../hooks";
import type { Selection } from "./Inspector";
import { Empty, ErrorNote } from "./ui";

const RUN_TEXT: Record<RunStatus, string> = {
  queued: "Queued",
  running: "Running",
  waiting_approval: "Waiting for approval",
  succeeded: "Finished",
  failed: "Failed",
  rejected: "Rejected",
  cancelled: "Cancelled",
};
const STEP_TEXT: Record<string, string> = {
  pending: "Not started",
  running: "Working",
  waiting: "Waiting",
  done: "Done",
  failed: "Failed",
  skipped: "Skipped",
};
const KIND_TEXT: Record<string, string> = {
  retrieve: "Find passages",
  extract: "Read details",
  review_table: "Review table",
  playbook_review: "Playbook check",
  llm: "Write",
  approval: "Approval",
  output_document: "Save as draft",
};
const POSITION_TEXT: Record<string, string> = {
  preferred: "Preferred",
  fallback: "Fallback",
  unacceptable: "Unacceptable",
  missing: "Missing",
};
const live = (s: RunStatus) => s === "queued" || s === "running";
const money = (n: number) => (n ? `$${n.toFixed(4)}` : "$0");

type View = { kind: "run"; id: string } | { kind: "start" } | { kind: "playbooks" } | null;

export function WorkflowsTab(props: {
  matterId: string;
  canManage: boolean;
  onInspect: (s: Selection) => void;
  onOpenDraft: (id: string) => void;
}) {
  const [view, setView] = useState<View>(null);
  const [rev, setRev] = useState(0);
  const runs = useLoad(() => api.runs(props.matterId), [props.matterId, rev]);
  const anyLive = (runs.data ?? []).some((r) => live(r.status));
  useInterval(() => setRev((r) => r + 1), 3000, anyLive);
  const waiting = (runs.data ?? []).filter((r) => r.status === "waiting_approval").length;

  return (
    <section className="reviews">
      <aside className="threads" aria-label="Workflow runs">
        <button className="btn btn-primary threads-new" onClick={() => setView({ kind: "start" })}>
          Start a workflow
        </button>
        <button className={`btn ${view?.kind === "playbooks" ? "is-on" : ""}`} onClick={() => setView({ kind: "playbooks" })}>
          Playbooks
        </button>
        {runs.error && <ErrorNote>{runs.error}</ErrorNote>}
        {runs.data?.length === 0 && <p className="muted threads-empty">No runs on this matter yet.</p>}
        {waiting > 0 && <p className="muted threads-empty">{waiting} waiting for approval</p>}
        <ul>
          {runs.data?.map((r) => (
            <li key={r.id}>
              <button
                className={`thread ${view?.kind === "run" && view.id === r.id ? "is-on" : ""}`}
                onClick={() => setView({ kind: "run", id: r.id })}
              >
                <span className="thread-title">{r.name}</span>
                <span className="thread-meta">
                  <span className={`run-dot run-${r.status}`} aria-hidden /> {RUN_TEXT[r.status]} · {r.steps_done}/{r.steps_total} ·{" "}
                  {when(r.created_at)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </aside>
      {view?.kind === "start" && (
        <StartRun
          matterId={props.matterId}
          onCancel={() => setView(null)}
          onStarted={(id) => {
            setView({ kind: "run", id });
            setRev((r) => r + 1);
          }}
          onPlaybooks={() => setView({ kind: "playbooks" })}
        />
      )}
      {view?.kind === "run" && (
        <RunView
          key={view.id}
          runId={view.id}
          onInspect={props.onInspect}
          onOpenDraft={props.onOpenDraft}
          onChanged={() => setRev((r) => r + 1)}
        />
      )}
      {view?.kind === "playbooks" && <Playbooks canManage={props.canManage} />}
      {view === null && (
        <Empty title="Run a piece of legal work end to end">
          A workflow chains steps: it reads the documents, writes with citations, stops for a partner's approval where
          one is needed, and saves the result as a draft. Every step shows what it read, what it produced, and what it
          cost.
        </Empty>
      )}
    </section>
  );
}

// ------------------------------------------------------------------ start

function StartRun(props: {
  matterId: string;
  onCancel: () => void;
  onStarted: (id: string) => void;
  onPlaybooks: () => void;
}) {
  const flows = useLoad(() => api.workflows(), []);
  const [slug, setSlug] = useState<string | null>(null);
  const chosen = flows.data?.find((f) => f.slug === slug) ?? null;
  return (
    <div className="review-create">
      <h2>Start a workflow</h2>
      {flows.error && <ErrorNote>{flows.error}</ErrorNote>}
      <div className="tpl-list" role="radiogroup" aria-label="Workflow">
        {flows.data?.map((f) => (
          <button
            key={f.slug}
            type="button"
            role="radio"
            aria-checked={slug === f.slug}
            className={`tpl ${slug === f.slug ? "is-on" : ""}`}
            onClick={() => setSlug(f.slug)}
          >
            <strong>{f.name}</strong>
            <span>{f.description}</span>
            <span className="muted">
              {f.steps.length} steps{f.steps.some((s) => s.type === "approval") ? ", with approval" : ""}
              {f.builtin ? "" : ` · firm version ${f.version}`}
            </span>
          </button>
        ))}
      </div>
      {chosen && (
        <InputsForm
          key={chosen.slug}
          flow={chosen}
          matterId={props.matterId}
          onCancel={props.onCancel}
          onStarted={props.onStarted}
          onPlaybooks={props.onPlaybooks}
        />
      )}
      {!chosen && (
        <div className="row">
          <button type="button" className="btn btn-quiet" onClick={props.onCancel}>
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}

function InputsForm(props: {
  flow: WorkflowSummary;
  matterId: string;
  onCancel: () => void;
  onStarted: (id: string) => void;
  onPlaybooks: () => void;
}) {
  const docs = useLoad(() => api.documents(props.matterId), [props.matterId]);
  const needsBook = props.flow.inputs.some((i) => i.type === "playbook");
  const books = useLoad(needsBook ? () => api.playbooks() : null, [needsBook]);
  const [values, setValues] = useState<Record<string, unknown>>(() =>
    Object.fromEntries(props.flow.inputs.map((i) => [i.key, i.default ?? (i.type === "documents" ? [] : "")])),
  );
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const usable = (docs.data ?? []).filter((d) => d.status === "indexed" || d.status === "partial");
  const set = (k: string, v: unknown) => setValues((x) => ({ ...x, [k]: v }));

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const clean = Object.fromEntries(Object.entries(values).filter(([, v]) => v !== "" && v !== null));
      const run = await api.startRun(props.matterId, props.flow.slug, clean);
      props.onStarted(run.id);
    } catch (e2) {
      setErr(errorText(e2));
    } finally {
      setBusy(false);
    }
  }

  const field = (i: WorkflowInput): ReactNode => {
    const v = values[i.key];
    switch (i.type) {
      case "textarea":
        return <textarea rows={3} value={String(v ?? "")} onChange={(e) => set(i.key, e.target.value)} />;
      case "number":
        return (
          <input
            type="number"
            min={i.min}
            max={i.max}
            value={String(v ?? "")}
            onChange={(e) => set(i.key, e.target.value === "" ? "" : Number(e.target.value))}
          />
        );
      case "choice":
        return (
          <select value={String(v ?? "")} onChange={(e) => set(i.key, e.target.value)}>
            {(i.choices ?? []).map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        );
      case "document":
        return (
          <select value={String(v ?? "")} onChange={(e) => set(i.key, e.target.value)}>
            <option value="">Choose a document…</option>
            {usable.map((d) => (
              <option key={d.id} value={d.id}>
                {d.title} ({label(d.doc_type)})
              </option>
            ))}
          </select>
        );
      case "documents": {
        const list = (v as string[]) ?? [];
        return (
          <div className="doc-pick card">
            {usable.map((d) => (
              <label key={d.id}>
                <input
                  type="checkbox"
                  checked={list.includes(d.id)}
                  onChange={(e) => set(i.key, e.target.checked ? [...list, d.id] : list.filter((x) => x !== d.id))}
                />{" "}
                {d.title} <span className="muted">· {label(d.doc_type)}</span>
              </label>
            ))}
          </div>
        );
      }
      case "playbook":
        return books.data && books.data.length === 0 ? (
          <p className="muted">
            Your firm has no playbooks yet.{" "}
            <button type="button" className="link" onClick={props.onPlaybooks}>
              Set one up
            </button>
          </p>
        ) : (
          <select value={String(v ?? "")} onChange={(e) => set(i.key, e.target.value)}>
            <option value="">Choose a playbook…</option>
            {(books.data ?? []).map((b) => (
              <option key={b.id} value={b.id}>
                {b.name} (v{b.version}, {b.clauses.length} clauses)
              </option>
            ))}
          </select>
        );
      default:
        return <input value={String(v ?? "")} onChange={(e) => set(i.key, e.target.value)} />;
    }
  };

  return (
    <form className="flow-form card" onSubmit={submit}>
      <ol className="flow-plan" aria-label="What this workflow does">
        {props.flow.steps.map((s) => (
          <li key={s.id}>
            {s.title} <span className="muted">· {KIND_TEXT[s.type] ?? s.type}</span>
          </li>
        ))}
      </ol>
      {props.flow.inputs.map((i) => (
        <label key={i.key} className="field">
          <span>
            {i.label}
            {i.required ? "" : " (optional)"}
          </span>
          {field(i)}
        </label>
      ))}
      {err && <ErrorNote>{err}</ErrorNote>}
      <div className="row">
        <button className="btn btn-primary" disabled={busy}>
          {busy ? "Starting…" : "Start"}
        </button>
        <button type="button" className="btn btn-quiet" onClick={props.onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

// ------------------------------------------------------------------ a run

function RunView(props: {
  runId: string;
  onInspect: (s: Selection) => void;
  onOpenDraft: (id: string) => void;
  onChanged: () => void;
}) {
  const [rev, setRev] = useState(0);
  const run = useLoad(() => api.run(props.runId), [props.runId, rev]);
  const r = run.data;
  useInterval(() => setRev((x) => x + 1), 2000, !!r && live(r.status));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function act(fn: () => Promise<RunDetail>) {
    setBusy(true);
    setErr(null);
    try {
      await fn();
      setRev((x) => x + 1);
      props.onChanged();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  if (run.error && !r) return <ErrorNote>{run.error}</ErrorNote>;
  if (!r) return <p className="muted">Loading…</p>;
  return (
    <div className="review">
      <header className="review-head">
        <div>
          <h2 className="flow-title">{r.name}</h2>
          <p className="muted">
            Started by {r.created_by.name} · {when(r.created_at)} · {r.input_tokens + r.output_tokens} tokens ·{" "}
            {money(r.cost_usd)}
          </p>
        </div>
        <span className={`run-chip run-${r.status}`}>{RUN_TEXT[r.status]}</span>
      </header>
      {r.job && live(r.status) && (
        <div className="job">
          <div className="bar">
            <div className="bar-fill" style={{ width: `${Math.max(4, Math.round((100 * r.steps_done) / r.steps_total))}%` }} />
          </div>
          <p className="muted">{r.job.current_step}</p>
        </div>
      )}
      {r.error && <ErrorNote>{r.error}</ErrorNote>}
      {err && <ErrorNote>{err}</ErrorNote>}
      <div className="row">
        {r.can_retry && (
          <button className="btn btn-primary" disabled={busy} onClick={() => act(() => api.retryRun(r.id))}>
            Retry from the failed step
          </button>
        )}
        {r.can_cancel && (
          <button className="btn btn-quiet" disabled={busy} onClick={() => act(() => api.cancelRun(r.id))}>
            Cancel run
          </button>
        )}
      </div>
      <ol className="timeline">
        {r.steps.map((s) => (
          <li key={s.id} className={`tl tl-${s.status}`}>
            <div className="tl-head">
              <span className="tl-no" aria-hidden>
                {s.position + 1}
              </span>
              <strong>{s.title}</strong>
              <span className="muted">{KIND_TEXT[s.type] ?? s.type}</span>
              <span className={`tl-status st-${s.status}`}>{STEP_TEXT[s.status]}</span>
              {s.model && (
                <span className="muted tl-cost">
                  {s.model} · {s.input_tokens + s.output_tokens} tokens · {money(s.cost_usd)}
                </span>
              )}
            </div>
            {s.error && <p className="cell-error">{s.error}</p>}
            {(s.status === "done" || s.status === "waiting" || s.type === "approval") && (
              <StepBody step={s} run={r} onInspect={props.onInspect} onOpenDraft={props.onOpenDraft} onDecided={() => act(async () => r)} />
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}

type Passage = { chunk_id: string; document_id: string; document_title: string; page_from: number; page_to: number; text?: string; excerpt?: string };

const pages = (a?: number | null, b?: number | null) => (a ? (a === b || !b ? `p. ${a}` : `pp. ${a}-${b}`) : "");

function Passages(props: { list: Passage[]; onInspect: (s: Selection) => void }) {
  if (!props.list.length) return <p className="muted">Nothing relevant was found in the documents.</p>;
  return (
    <ul className="ans-sources">
      {props.list.map((p, i) => (
        <li key={p.chunk_id}>
          <button
            className="src"
            onClick={() => props.onInspect({ kind: "chunk", docId: p.document_id, chunkId: p.chunk_id, terms: [] })}
          >
            <span className="src-title">
              [{i + 1}] {p.document_title} · {pages(p.page_from, p.page_to)}
            </span>
            <span className="src-excerpt">{(p.excerpt ?? p.text ?? "").slice(0, 220)}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function StepBody(props: {
  step: WorkflowStep;
  run: RunDetail;
  onInspect: (s: Selection) => void;
  onOpenDraft: (id: string) => void;
  onDecided: () => void;
}) {
  const { step: s, run } = props;
  const o = s.output;
  const docId = String(o.document_id ?? "");
  const openChunk = (chunk: string | null) =>
    chunk && props.onInspect({ kind: "chunk", docId, chunkId: chunk, terms: [] });

  switch (s.type) {
    case "retrieve":
      return <Passages list={o.passages ?? []} onInspect={props.onInspect} />;
    case "extract":
      return (
        <dl className="facts flow-facts">
          {Object.entries((o.fields ?? {}) as Record<string, any>).map(([k, f]) => (
            <div key={k}>
              <dt>{f.label}</dt>
              <dd>
                {f.chunk_id ? (
                  <button className="link" onClick={() => openChunk(f.chunk_id)}>
                    {f.answer}
                  </button>
                ) : (
                  <span className="muted">{f.answer ?? "Not stated"}</span>
                )}{" "}
                {f.page_from && <span className="muted">({pages(f.page_from, f.page_to)})</span>}
                {f.verified === false && <span className="v v-partial" title={f.flag ?? ""}>check</span>}
              </dd>
            </div>
          ))}
        </dl>
      );
    case "playbook_review":
      return (
        <div className="grid-wrap">
          <p className="muted">
            {o.document_title} against {o.playbook_name} (v{o.playbook_version}), acting for the {o.side}.{" "}
            {Object.entries((o.counts ?? {}) as Record<string, number>)
              .filter(([, n]) => n)
              .map(([k, n]) => `${n} ${POSITION_TEXT[k].toLowerCase()}`)
              .join(", ")}
          </p>
          <table className="table pb-table">
            <thead>
              <tr>
                <th>Clause</th>
                <th>Position</th>
                <th>The agreement says</th>
                <th>Why, and what to ask for</th>
              </tr>
            </thead>
            <tbody>
              {(o.clauses ?? []).map((c: any) => (
                <tr key={c.key} className="is-static">
                  <td>
                    <strong>{c.title}</strong>
                  </td>
                  <td>
                    <span className={`pos pos-${c.position}`}>{POSITION_TEXT[c.position] ?? c.position}</span>
                  </td>
                  <td>
                    {c.chunk_id ? (
                      <button className="link" onClick={() => openChunk(c.chunk_id)}>
                        {c.answer}
                      </button>
                    ) : (
                      <span className="muted">Not dealt with</span>
                    )}{" "}
                    {c.page_from && <span className="muted">({pages(c.page_from, c.page_to)})</span>}
                  </td>
                  <td>
                    {c.reason}
                    {c.suggested_wording && (
                      <p className="pb-wording">
                        <span className="muted">Suggested: </span>
                        {c.suggested_wording}
                      </p>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case "review_table":
      return (
        <p className="muted">
          {o.documents} documents × {(o.columns ?? []).length} questions ({(o.columns ?? []).join(", ")}). Open it under
          Review tables as “{o.title}”.
        </p>
      );
    case "llm":
      return <Written output={o} onInspect={props.onInspect} />;
    case "approval": {
      const a = run.approvals.find((x) => x.step_id === s.id);
      return a ? <ApprovalCard approval={a} run={run} onDecided={props.onDecided} /> : null;
    }
    case "output_document":
      return (
        <p>
          Saved as a draft{o.approved ? ", already approved" : ""}:{" "}
          <button className="link" onClick={() => props.onOpenDraft(String(o.draft_id))}>
            {o.title}
          </button>{" "}
          <span className="muted">· {o.sources} sources cited</span>
        </p>
      );
    default:
      return <pre className="mono">{JSON.stringify(o, null, 2)}</pre>;
  }
}

function Written(props: { output: Record<string, any>; onInspect: (s: Selection) => void }) {
  const o = props.output;
  const sources: Passage[] = o.sources ?? [];
  const number = new Map(sources.map((src: any, i) => [String(src.key), i + 1]));
  const blocks = new Map<number, any[]>();
  for (const st of o.sentences ?? []) blocks.set(st.block, [...(blocks.get(st.block) ?? []), st]);
  const v = o.verification ?? {};
  return (
    <div className="answer flow-written">
      {[...blocks.entries()].map(([b, list]) => (
        <p key={b} className="ans-block">
          {list[0]?.prefix && <span className="ans-prefix">{list[0].prefix}</span>}
          {list.map((st: any, i: number) => (
            <span key={i} className={`sent s-${st.label}`} title={st.reason || undefined}>
              {st.text}
              {st.cites.map((k: string) => (
                <sup key={k} className="cite">
                  [{number.get(k) ?? k}]
                </sup>
              ))}{" "}
            </span>
          ))}
        </p>
      ))}
      {!o.sentences?.length && <p>{o.text}</p>}
      {v.verifier && v.verifier !== "none" && (
        <p className="muted">
          Checked against the sources: {v.supported} supported, {v.partial} partly, {v.unsupported} not supported,{" "}
          {v.uncited} without a citation.
        </p>
      )}
      {sources.length > 0 && <Passages list={sources} onInspect={props.onInspect} />}
    </div>
  );
}

function ApprovalCard(props: { approval: RunDetail["approvals"][number]; run: RunDetail; onDecided: () => void }) {
  const a = props.approval;
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function decide(d: "approve" | "reject") {
    setBusy(true);
    setErr(null);
    try {
      await api.decideApproval(a.id, d, comment.trim() || undefined);
      props.onDecided();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  const who = a.required_role === "firm_partner" ? "a partner" : "the matter lead or a partner on the matter";
  if (a.status === "cancelled") return <p className="muted">Cancelled with the run.</p>;
  if (a.status !== "pending")
    return (
      <p className={`banner ${a.status === "approved" ? "banner-ok" : "banner-warn"}`}>
        {label(a.status)} by {a.decided_by?.name ?? "someone"}
        {a.decided_at ? ` · ${when(a.decided_at)}` : ""}
        {a.comment ? `: “${a.comment}”` : ""}
      </p>
    );
  return (
    <div className="banner banner-act approval">
      <p>
        <strong>{a.message}</strong>
        <br />
        <span className="muted">
          Requested by {a.requested_by.name} · needs {who}
          {props.run.created_by.id === a.requested_by.id ? ", not the person who started the run" : ""}.
        </span>
      </p>
      {a.can_decide ? (
        <>
          <textarea
            rows={2}
            placeholder="Comment (needed if you reject)"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
          />
          <div className="row">
            <button className="btn btn-primary" disabled={busy} onClick={() => decide("approve")}>
              Approve and continue
            </button>
            <button className="btn btn-danger" disabled={busy || !comment.trim()} onClick={() => decide("reject")}>
              Reject
            </button>
          </div>
        </>
      ) : (
        <p className="muted">You cannot decide this one. Switch identity to the matter lead to approve it.</p>
      )}
      {err && <ErrorNote>{err}</ErrorNote>}
    </div>
  );
}

// ------------------------------------------------------------------ playbooks

const EMPTY_CLAUSE: Clause = { key: "", title: "", question: "", preferred: "", fallback: "", unacceptable: "", guidance: "" };

function Playbooks(props: { canManage: boolean }) {
  const [rev, setRev] = useState(0);
  const books = useLoad(() => api.playbooks(), [rev]);
  const templates = useLoad(() => api.playbookTemplates(), []);
  const [open, setOpen] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const book = books.data?.find((b) => b.id === open) ?? null;

  async function copy(id: string) {
    setErr(null);
    try {
      const b = await api.createPlaybook({ from_template: id });
      setRev((r) => r + 1);
      setOpen(b.id);
    } catch (e) {
      setErr(errorText(e));
    }
  }

  return (
    <div className="review">
      <header className="review-head">
        <div>
          <h2 className="flow-title">Playbooks</h2>
          <p className="muted">
            Your firm's standard positions, clause by clause. The contract review workflow compares agreements with
            them. {props.canManage ? "" : "Partners and admins can change them."}
          </p>
        </div>
      </header>
      {err && <ErrorNote>{err}</ErrorNote>}
      {books.error && <ErrorNote>{books.error}</ErrorNote>}
      <div className="tpl-list">
        {books.data?.map((b) => (
          <button key={b.id} className={`tpl ${open === b.id ? "is-on" : ""}`} onClick={() => setOpen(b.id)}>
            <strong>{b.name}</strong>
            <span>{b.description}</span>
            <span className="muted">
              {b.clauses.length} clauses · v{b.version} · {b.updated_by.name}
            </span>
          </button>
        ))}
      </div>
      {props.canManage && (
        <div className="row">
          <span className="muted">Copy a sample:</span>
          {templates.data?.map((t) => (
            <button key={t.id} className="btn btn-small" onClick={() => copy(t.id)}>
              {t.name}
            </button>
          ))}
        </div>
      )}
      {books.data?.length === 0 && !props.canManage && <p className="muted">No playbooks yet.</p>}
      {book && (
        <PlaybookEditor
          key={`${book.id}-${book.version}`}
          book={book}
          canManage={props.canManage}
          onSaved={() => setRev((r) => r + 1)}
          onDeleted={() => {
            setOpen(null);
            setRev((r) => r + 1);
          }}
        />
      )}
    </div>
  );
}

function PlaybookEditor(props: { book: Playbook; canManage: boolean; onSaved: () => void; onDeleted: () => void }) {
  const [draft, setDraft] = useState<PlaybookInput>({
    name: props.book.name,
    contract_type: props.book.contract_type,
    description: props.book.description,
    clauses: props.book.clauses,
  });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const ro = !props.canManage;
  const setClause = (i: number, k: keyof Clause, v: string) =>
    setDraft((d) => ({ ...d, clauses: d.clauses.map((c, j) => (j === i ? { ...c, [k]: v } : c)) }));

  async function save() {
    setBusy(true);
    setErr(null);
    try {
      await api.updatePlaybook(props.book.id, draft);
      props.onSaved();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    setBusy(true);
    try {
      await api.deletePlaybook(props.book.id);
      props.onDeleted();
    } catch (e) {
      setErr(errorText(e));
      setBusy(false);
    }
  }

  return (
    <div className="card pb-edit">
      <label className="field">
        <span>Name</span>
        <input value={draft.name} readOnly={ro} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
      </label>
      {draft.clauses.map((c, i) => (
        <fieldset key={i} className="pb-clause">
          <div className="pb-clause-head">
            <input
              aria-label="Clause title"
              value={c.title}
              readOnly={ro}
              placeholder="Clause title"
              onChange={(e) => setClause(i, "title", e.target.value)}
            />
            <input
              aria-label="Key"
              className="mono"
              value={c.key}
              readOnly={ro}
              placeholder="key"
              onChange={(e) => setClause(i, "key", e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_"))}
            />
            {!ro && (
              <button
                type="button"
                className="link"
                onClick={() => setDraft({ ...draft, clauses: draft.clauses.filter((_, j) => j !== i) })}
              >
                remove
              </button>
            )}
          </div>
          {(
            [
              ["question", "Question to ask of the agreement"],
              ["preferred", "Preferred position"],
              ["fallback", "Fallback (acceptable)"],
              ["unacceptable", "Unacceptable"],
              ["guidance", "Guidance for the reviewer"],
            ] as [keyof Clause, string][]
          ).map(([k, name]) => (
            <label key={k} className={`field pb-${k}`}>
              <span>{name}</span>
              <textarea rows={1} value={c[k]} readOnly={ro} onChange={(e) => setClause(i, k, e.target.value)} />
            </label>
          ))}
        </fieldset>
      ))}
      {err && <ErrorNote>{err}</ErrorNote>}
      {!ro && (
        <div className="row">
          <button className="btn btn-primary" disabled={busy} onClick={save}>
            Save as version {props.book.version + 1}
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => setDraft({ ...draft, clauses: [...draft.clauses, { ...EMPTY_CLAUSE }] })}
          >
            Add clause
          </button>
          <span className="grow" />
          <button className="btn btn-danger-text" disabled={busy} onClick={remove}>
            Delete playbook
          </button>
        </div>
      )}
    </div>
  );
}
