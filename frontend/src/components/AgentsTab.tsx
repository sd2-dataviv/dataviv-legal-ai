import { useState, type FormEvent } from "react";
import {
  api,
  errorText,
  label,
  when,
  type AgentRunDetail,
  type AgentRunStatus,
  type AgentStep,
  type AgentSummary,
  type Approval,
  type MemoryKind,
} from "../api";
import { useInterval, useLoad } from "../hooks";
import type { Selection } from "./Inspector";
import { Empty, ErrorNote } from "./ui";

const RUN_TEXT: Record<AgentRunStatus, string> = {
  queued: "Queued",
  running: "Working",
  waiting_approval: "Waiting for approval",
  waiting_subagents: "Waiting for subagents",
  succeeded: "Finished",
  stopped: "Stopped (budget)",
  failed: "Failed",
  cancelled: "Cancelled",
};
const STEP_TEXT: Record<AgentStep["status"], string> = {
  running: "Working",
  waiting_approval: "Waiting for approval",
  approved: "Approved",
  waiting: "Waiting for subagents",
  done: "Done",
  rejected: "Rejected",
  failed: "Did not work",
};
// Step dots reuse the workflow timeline's colours.
const STEP_CLASS: Record<AgentStep["status"], string> = {
  running: "running",
  waiting_approval: "waiting",
  approved: "running",
  waiting: "waiting",
  done: "done",
  rejected: "failed",
  failed: "failed",
};
const TOOL_TEXT: Record<string, string> = {
  recall_memory: "Recall memory",
  list_documents: "List documents",
  search_documents: "Search",
  read_passage: "Read passage",
  read_document: "Read document",
  remember_fact: "Save to memory",
  create_draft: "Create draft",
  delegate_to_subagents: "Hand to subagents",
  final_answer: "Answer",
};
const POLICY_TEXT = { auto: "runs at once", confirm: "you confirm", manager: "lead approves" } as const;
const live = (s: AgentRunStatus) => s === "queued" || s === "running" || s === "waiting_subagents";
const money = (n: number) => (n ? `$${n.toFixed(4)}` : "$0");
const runClass = (s: AgentRunStatus) =>
  s === "waiting_subagents" ? "running" : s === "stopped" ? "cancelled" : s === "succeeded" ? "succeeded" : s;

type View = { kind: "run"; id: string } | { kind: "start" } | { kind: "memory" } | null;

export function AgentsTab(props: {
  matterId: string;
  onInspect: (s: Selection) => void;
  onOpenDraft: (id: string) => void;
}) {
  const [view, setView] = useState<View>(null);
  const [rev, setRev] = useState(0);
  const runs = useLoad(() => api.agentRuns(props.matterId), [props.matterId, rev]);
  useInterval(() => setRev((r) => r + 1), 3000, (runs.data ?? []).some((r) => live(r.status)));

  return (
    <section className="reviews">
      <aside className="threads" aria-label="Agent runs">
        <button className="btn btn-primary threads-new" onClick={() => setView({ kind: "start" })}>
          Start an agent
        </button>
        <button className={`btn ${view?.kind === "memory" ? "is-on" : ""}`} onClick={() => setView({ kind: "memory" })}>
          Matter memory
        </button>
        {runs.error && <ErrorNote>{runs.error}</ErrorNote>}
        {runs.data?.length === 0 && <p className="muted threads-empty">No agent runs on this matter yet.</p>}
        <ul>
          {runs.data?.map((r) => (
            <li key={r.id}>
              <button
                className={`thread ${view?.kind === "run" && view.id === r.id ? "is-on" : ""}`}
                onClick={() => setView({ kind: "run", id: r.id })}
              >
                <span className="thread-title">{r.goal}</span>
                <span className="thread-meta">
                  <span className={`run-dot run-${runClass(r.status)}`} aria-hidden /> {r.name} · {RUN_TEXT[r.status]} ·{" "}
                  {when(r.created_at)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </aside>
      {view?.kind === "start" && (
        <StartAgent
          matterId={props.matterId}
          onCancel={() => setView(null)}
          onStarted={(id) => {
            setView({ kind: "run", id });
            setRev((r) => r + 1);
          }}
        />
      )}
      {view?.kind === "run" && (
        <AgentRun
          key={view.id}
          runId={view.id}
          onInspect={props.onInspect}
          onOpenDraft={props.onOpenDraft}
          onOpenRun={(id) => setView({ kind: "run", id })}
          onChanged={() => setRev((r) => r + 1)}
        />
      )}
      {view?.kind === "memory" && <Memory matterId={props.matterId} onInspect={props.onInspect} />}
      {view === null && (
        <Empty title="Give an agent a goal; follow every step it takes">
          An agent searches and reads this matter's documents as you, never beyond your access. It can save facts to the
          matter memory and draft documents; the actions its policy marks are held until a person approves them. Each
          step shows what it was thinking, what it did and what it found, and what it cost.
        </Empty>
      )}
    </section>
  );
}

// ------------------------------------------------------------------ start

function StartAgent(props: { matterId: string; onCancel: () => void; onStarted: (id: string) => void }) {
  const agents = useLoad(() => api.agents(), []);
  const docs = useLoad(() => api.documents(props.matterId), [props.matterId]);
  const [slug, setSlug] = useState<string | null>(null);
  const [goal, setGoal] = useState("");
  const [which, setWhich] = useState<"all" | "some">("all");
  const [chosen, setChosen] = useState<string[]>([]);
  const [steps, setSteps] = useState<number | "">("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const agent: AgentSummary | undefined = agents.data?.find((a) => a.slug === slug);
  const usable = (docs.data ?? []).filter((d) => d.status === "indexed" || d.status === "partial");

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!agent) return;
    setBusy(true);
    setErr(null);
    try {
      const run = await api.startAgent(props.matterId, {
        agent: agent.slug,
        goal: goal.trim(),
        document_ids: which === "some" ? chosen : null,
        max_steps: steps === "" ? undefined : steps,
      });
      props.onStarted(run.id);
    } catch (e2) {
      setErr(errorText(e2));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="review-create" onSubmit={submit}>
      <h2>Start an agent</h2>
      {agents.error && <ErrorNote>{agents.error}</ErrorNote>}
      <div className="tpl-list" role="radiogroup" aria-label="Agent">
        {agents.data?.map((a) => (
          <button
            key={a.slug}
            type="button"
            role="radio"
            aria-checked={slug === a.slug}
            className={`tpl ${slug === a.slug ? "is-on" : ""}`}
            onClick={() => setSlug(a.slug)}
          >
            <strong>{a.name}</strong>
            <span>{a.description}</span>
            <span className="muted">
              Up to {a.max_steps} steps, ${a.max_cost_usd.toFixed(2)}
              {a.builtin ? "" : ` · firm version ${a.version}`}
            </span>
          </button>
        ))}
      </div>
      {agent && (
        <div className="flow-form card">
          <ul className="agent-tools" aria-label="What this agent may do">
            {agent.tools.map((t) => (
              <li key={t.name} className={`agent-tool pol-${t.policy}`}>
                {TOOL_TEXT[t.name] ?? t.name}
                {t.policy !== "auto" && <span className="muted"> · {POLICY_TEXT[t.policy]}</span>}
              </li>
            ))}
          </ul>
          <label className="field">
            <span>Goal</span>
            <textarea rows={3} value={goal} placeholder={agent.goal_hint} onChange={(e) => setGoal(e.target.value)} />
          </label>
          <fieldset className="field">
            <span>Documents</span>
            <label className="radio">
              <input type="radio" checked={which === "all"} onChange={() => setWhich("all")} /> The whole matter
            </label>
            <label className="radio">
              <input type="radio" checked={which === "some"} onChange={() => setWhich("some")} /> Only these documents
            </label>
            {which === "some" && (
              <div className="doc-pick card">
                {usable.map((d) => (
                  <label key={d.id}>
                    <input
                      type="checkbox"
                      checked={chosen.includes(d.id)}
                      onChange={(e) => setChosen(e.target.checked ? [...chosen, d.id] : chosen.filter((x) => x !== d.id))}
                    />{" "}
                    {d.title} <span className="muted">· {label(d.doc_type)}</span>
                  </label>
                ))}
              </div>
            )}
          </fieldset>
          <label className="field field-narrow">
            <span>Step limit (optional, at most {agent.max_steps})</span>
            <input
              type="number"
              min={1}
              max={agent.max_steps}
              value={steps}
              onChange={(e) => setSteps(e.target.value === "" ? "" : Number(e.target.value))}
            />
          </label>
          {err && <ErrorNote>{err}</ErrorNote>}
          <div className="row">
            <button className="btn btn-primary" disabled={busy || goal.trim().length < 5 || (which === "some" && !chosen.length)}>
              {busy ? "Starting…" : "Start"}
            </button>
            <button type="button" className="btn btn-quiet" onClick={props.onCancel}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </form>
  );
}

// ------------------------------------------------------------------ a run

function AgentRun(props: {
  runId: string;
  onInspect: (s: Selection) => void;
  onOpenDraft: (id: string) => void;
  onOpenRun: (id: string) => void;
  onChanged: () => void;
}) {
  const [rev, setRev] = useState(0);
  const run = useLoad(() => api.agentRun(props.runId), [props.runId, rev]);
  const r = run.data;
  useInterval(() => setRev((x) => x + 1), 2000, !!r && live(r.status));
  const [err, setErr] = useState<string | null>(null);
  const reload = () => {
    setRev((x) => x + 1);
    props.onChanged();
  };

  if (run.error && !r) return <ErrorNote>{run.error}</ErrorNote>;
  if (!r) return <p className="muted">Loading…</p>;
  const keys = new Map(r.sources.map((s, i) => [s.key, i + 1]));
  const open = (key: string) => {
    const s = r.sources.find((x) => x.key === key);
    if (s) props.onInspect({ kind: "chunk", docId: s.document_id, chunkId: s.chunk_id, terms: [] });
  };

  return (
    <div className="review">
      <header className="review-head">
        <div>
          <h2 className="flow-title">{r.goal}</h2>
          <p className="muted">
            {r.name}
            {r.parent_run_id && (
              <>
                {" "}
                · subagent of{" "}
                <button className="link" onClick={() => props.onOpenRun(r.parent_run_id!)}>
                  its main run
                </button>
              </>
            )}{" "}
            · started by {r.created_by.name} {when(r.created_at)} · step {r.steps_used} of {r.max_steps} · {money(r.cost_usd)} of $
            {r.max_cost_usd.toFixed(2)}
          </p>
        </div>
        <span className={`run-chip run-${runClass(r.status)}`}>{RUN_TEXT[r.status]}</span>
      </header>
      <div className="bar" aria-label="Budget used">
        <div className="bar-fill" style={{ width: `${Math.min(100, Math.round((100 * r.steps_used) / r.max_steps))}%` }} />
      </div>
      {r.error && <ErrorNote>{r.error}</ErrorNote>}
      {err && <ErrorNote>{err}</ErrorNote>}
      {r.can_cancel && (
        <div className="row">
          <button
            className="btn btn-quiet"
            onClick={async () => {
              setErr(null);
              try {
                await api.cancelAgent(r.id);
                reload();
              } catch (e) {
                setErr(errorText(e));
              }
            }}
          >
            Cancel run
          </button>
        </div>
      )}

      {r.answer && (
        <div className="answer agent-answer">
          {[...groupBlocks(r.sentences).entries()].map(([b, list]) => (
            <p key={b} className="ans-block">
              {list[0]?.prefix && <span className="ans-prefix">{list[0].prefix}</span>}
              {list.map((st, i) => (
                <span key={i} className={`sent s-${st.label}`} title={st.reason || undefined}>
                  {st.text}
                  {st.cites.map((k) => (
                    <sup key={k} className="cite">
                      <button className="link" onClick={() => open(k)}>
                        [{keys.get(k) ?? k}]
                      </button>
                    </sup>
                  ))}{" "}
                </span>
              ))}
            </p>
          ))}
          {r.verification?.verifier && (
            <p className="muted">
              Checked against the passages: {r.verification.supported} supported, {r.verification.partial} partly,{" "}
              {r.verification.unsupported} not supported, {r.verification.uncited} without a citation.
            </p>
          )}
        </div>
      )}

      <ol className="timeline">
        {r.steps.map((s) => (
          <li key={s.id} className={`tl tl-${STEP_CLASS[s.status]}`}>
            <div className="tl-head">
              <span className="tl-no" aria-hidden>
                {s.position + 1}
              </span>
              <strong>{TOOL_TEXT[s.tool] ?? s.tool}</strong>
              <span className={`tl-status st-${STEP_CLASS[s.status]}`}>{STEP_TEXT[s.status]}</span>
              {s.model && (
                <span className="muted tl-cost">
                  {s.input_tokens + s.output_tokens} tokens · {money(s.cost_usd)}
                </span>
              )}
            </div>
            {s.thought && <p className="agent-thought">{s.thought}</p>}
            {s.tool !== "final_answer" && Object.keys(s.arguments).length > 0 && <Args args={s.arguments} />}
            {s.status === "waiting_approval" && (
              <AgentApproval approval={r.approvals.find((a) => a.step_id === s.id)} onDecided={reload} />
            )}
            {s.result?.decision && (
              <p className="muted">
                {label(String(s.result.decision))} by {String(s.result.decided_by)}
                {s.result.comment ? `: “${String(s.result.comment)}”` : ""}
              </p>
            )}
            {s.result?.draft_id && (
              <p>
                Draft saved:{" "}
                <button className="link" onClick={() => props.onOpenDraft(String(s.result.draft_id))}>
                  {String(s.result.title ?? "open it")}
                </button>
              </p>
            )}
            {s.tool === "delegate_to_subagents" && r.children.length > 0 && (
              <ul className="agent-children">
                {r.children.map((c) => (
                  <li key={c.id}>
                    <button className="link" onClick={() => props.onOpenRun(c.id)}>
                      {c.goal.split("Document: ").pop()}
                    </button>{" "}
                    <span className="muted">
                      · {RUN_TEXT[c.status]} · {c.steps_used} steps · {money(c.cost_usd)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {s.observation && s.tool !== "final_answer" && (
              <details className="agent-obs">
                <summary>What it found</summary>
                <pre>{s.observation}</pre>
              </details>
            )}
          </li>
        ))}
      </ol>
      {r.sources.length > 0 && (
        <details className="agent-obs">
          <summary>{r.sources.length} passages read</summary>
          <ul className="ans-sources">
            {r.sources.map((s) => (
              <li key={s.key}>
                <button className="src" onClick={() => open(s.key)}>
                  <span className="src-title">
                    [{keys.get(s.key)}] {s.document_title} · p. {s.page_from}
                    {s.page_to !== s.page_from ? `-${s.page_to}` : ""}
                  </span>
                  <span className="src-excerpt">{s.excerpt}</span>
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function groupBlocks(sentences: AgentRunDetail["sentences"]) {
  const blocks = new Map<number, AgentRunDetail["sentences"]>();
  for (const st of sentences) blocks.set(st.block, [...(blocks.get(st.block) ?? []), st]);
  return blocks;
}

function Args(props: { args: Record<string, unknown> }) {
  return (
    <dl className="facts agent-args">
      {Object.entries(props.args).map(([k, v]) => (
        <div key={k}>
          <dt>{label(k)}</dt>
          <dd className={k === "content" ? "agent-long" : undefined}>{Array.isArray(v) ? `${v.length} items` : String(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

function AgentApproval(props: { approval?: Approval; onDecided: () => void }) {
  const a = props.approval;
  const [comment, setComment] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!a) return null;
  async function decide(d: "approve" | "reject") {
    setBusy(true);
    setErr(null);
    try {
      await api.decideApproval(a!.id, d, comment.trim() || undefined);
      props.onDecided();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  const who = a.required_role === "matter_editor" ? "anyone who can edit the matter" : "the matter lead or a partner, not the person who started it";
  return (
    <div className="banner banner-act approval">
      <p>
        <strong>{a.message}</strong>
        <br />
        <span className="muted">Needs {who}. If you reject it, the agent is told your comment and carries on.</span>
      </p>
      {a.can_decide ? (
        <>
          <textarea rows={2} placeholder="Comment for the agent (optional)" value={comment} onChange={(e) => setComment(e.target.value)} />
          <div className="row">
            <button className="btn btn-primary" disabled={busy} onClick={() => decide("approve")}>
              Approve
            </button>
            <button className="btn btn-danger" disabled={busy} onClick={() => decide("reject")}>
              Reject
            </button>
          </div>
        </>
      ) : (
        <p className="muted">You cannot decide this one; switch identity to someone who can.</p>
      )}
      {err && <ErrorNote>{err}</ErrorNote>}
    </div>
  );
}

// ------------------------------------------------------------------ matter memory

const KINDS: MemoryKind[] = ["fact", "event", "party", "deadline", "note"];

function Memory(props: { matterId: string; onInspect: (s: Selection) => void }) {
  const [q, setQ] = useState("");
  const [rev, setRev] = useState(0);
  const items = useLoad(() => api.memory(props.matterId, q.trim() || undefined), [props.matterId, rev]);
  const [kind, setKind] = useState<MemoryKind>("note");
  const [subject, setSubject] = useState("");
  const [content, setContent] = useState("");
  const [date, setDate] = useState("");
  const [err, setErr] = useState<string | null>(null);

  async function add(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    try {
      await api.addMemory(props.matterId, { kind, subject: subject.trim(), content: content.trim(), event_date: date || null });
      setSubject("");
      setContent("");
      setDate("");
      setRev((r) => r + 1);
    } catch (e2) {
      setErr(errorText(e2));
    }
  }
  async function remove(id: string) {
    setErr(null);
    try {
      await api.deleteMemory(id);
      setRev((r) => r + 1);
    } catch (e) {
      setErr(errorText(e));
    }
  }

  return (
    <div className="review">
      <header className="review-head">
        <div>
          <h2 className="flow-title">Matter memory</h2>
          <p className="muted">Facts, events, parties, deadlines and notes kept for this matter. Agents read them first, and save what they find with its source.</p>
        </div>
      </header>
      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault();
          setRev((r) => r + 1);
        }}
      >
        <input className="search-input" placeholder="Search the memory" value={q} onChange={(e) => setQ(e.target.value)} />
        <button className="btn">Search</button>
      </form>
      {(items.error || err) && <ErrorNote>{items.error || err}</ErrorNote>}
      {items.data?.length === 0 && <p className="muted">Nothing saved{q ? " matching that" : " yet"}.</p>}
      {!!items.data?.length && (
        <table className="table">
          <thead>
            <tr>
              <th>Date</th>
              <th>What</th>
              <th>Source</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.data.map((m) => (
              <tr key={m.id} className="is-static">
                <td className="nowrap">{m.event_date ? new Date(m.event_date).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : ""}</td>
                <td>
                  <span className={`mem-kind mem-${m.kind}`}>{label(m.kind)}</span> <strong>{m.subject}</strong>
                  <br />
                  {m.content}
                </td>
                <td>
                  {m.chunk_id && m.document_id ? (
                    <button className="link" onClick={() => props.onInspect({ kind: "chunk", docId: m.document_id!, chunkId: m.chunk_id!, terms: [] })}>
                      {m.document_title}, p. {m.page_from}
                    </button>
                  ) : (
                    <span className="muted">{m.document_title ?? "—"}</span>
                  )}
                  <br />
                  <span className="muted">
                    {m.source === "agent" ? "saved by an agent for " : ""}
                    {m.created_by.name}
                  </span>
                </td>
                <td>
                  <button className="btn-danger-text" onClick={() => remove(m.id)}>
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <form className="card flow-form" onSubmit={add}>
        <strong>Add to the memory</strong>
        <div className="row">
          <select value={kind} onChange={(e) => setKind(e.target.value as MemoryKind)} aria-label="Kind">
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {label(k)}
              </option>
            ))}
          </select>
          <input placeholder="Subject" value={subject} onChange={(e) => setSubject(e.target.value)} aria-label="Subject" />
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} aria-label="Date" />
        </div>
        <textarea rows={2} placeholder="What should the team (and agents) remember?" value={content} onChange={(e) => setContent(e.target.value)} />
        <div className="row">
          <button className="btn btn-primary" disabled={subject.trim().length < 2 || content.trim().length < 2}>
            Save
          </button>
        </div>
      </form>
    </div>
  );
}
