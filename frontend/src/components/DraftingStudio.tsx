// Drafting studio add-on: write contracts, legal notices, affidavits, letters and other documents from a
// template and the lawyer's facts, without a matter. Firm-wide history; download as Word.

import { useMemo, useState, type FormEvent } from "react";
import { errorText, when, type Identity } from "../api";
import { useLoad } from "../hooks";
import { studio, type StudioDetail, type StudioDoc, type StudioEvent, type StudioTemplate } from "../studio-api";
import { Empty, ErrorNote } from "./ui";
import "./studio.css";

type Live = { id: string | null; title: string; text: string; notice: string | null; error: string | null };

const STATUS_TEXT: Record<string, string> = { streaming: "Writing", complete: "Ready", failed: "Failed" };

export function DraftingStudio({ who }: { who: Identity }) {
  const templates = useLoad(() => studio.templates(), []);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [mine, setMine] = useState(false);
  const [rev, setRev] = useState(0);
  const history = useLoad(() => studio.documents({ q: query || undefined, mine }), [query, mine, rev]);
  const [openId, setOpenId] = useState<string | null>(null);
  const [live, setLive] = useState<Live | null>(null);
  const [writing, setWriting] = useState(false);

  /** Shared by "write" and "revise": follow the stream, then open the stored document. */
  const follow = async (start: (onEvent: (e: StudioEvent) => void) => Promise<void>, title: string) => {
    setWriting(true);
    setLive({ id: null, title, text: "", notice: null, error: null });
    try {
      await start((e) => {
        if (e.type === "meta") {
          setOpenId(e.data.document_id);
          setLive((l) => l && { ...l, id: e.data.document_id, title: e.data.title });
          setRev((r) => r + 1);
        } else if (e.type === "delta") setLive((l) => l && { ...l, text: l.text + e.data.text, notice: null });
        else if (e.type === "notice") setLive((l) => l && { ...l, notice: e.data.text });
        else if (e.type === "error") setLive((l) => l && { ...l, error: e.data.detail });
        else if (e.type === "done") setLive(null);
      });
    } catch (err) {
      setLive((l) => (l ? { ...l, error: errorText(err) } : l));
    } finally {
      setWriting(false);
      setRev((r) => r + 1);
    }
  };

  if (templates.error) {
    return (
      <div className="studio studio-blocked">
        <ErrorNote>Drafting studio unavailable: {templates.error}</ErrorNote>
      </div>
    );
  }

  return (
    <div className="studio">
      <aside className="studio-rail" aria-label="Studio history">
        <button
          className="btn btn-primary studio-new"
          onClick={() => {
            setOpenId(null);
            setLive(null);
          }}
          disabled={writing}
        >
          New document
        </button>
        <form
          className="studio-search"
          onSubmit={(e) => {
            e.preventDefault();
            setQuery(search.trim());
          }}
        >
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search parties, titles, text"
            aria-label="Search studio documents"
          />
          <label className="studio-mine">
            <input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> Mine only
          </label>
        </form>
        <p className="studio-rail-head">Firm history{history.data ? ` · ${history.data.length}` : ""}</p>
        {history.error && <ErrorNote>{history.error}</ErrorNote>}
        {history.data && history.data.length === 0 && (
          <p className="muted studio-rail-empty">{query ? "Nothing matches." : "No documents yet."}</p>
        )}
        <ul className="studio-list">
          {(history.data ?? []).map((d) => (
            <HistoryItem key={d.id} doc={d} on={openId !== null && d.family_id === familyOf(openId, history.data)} onOpen={() => setOpenId(d.id)} />
          ))}
        </ul>
      </aside>

      <section className="studio-main">
        {openId === null && live === null ? (
          <Composer
            templates={templates.data ?? []}
            loading={templates.loading}
            busy={writing}
            onWrite={(body, title) => follow((cb) => studio.generate(body, cb), title)}
          />
        ) : (
          <DocView
            key={openId ?? "pending"}
            id={openId}
            live={live && (live.id === null || live.id === openId) ? live : null}
            who={who}
            rev={rev}
            busy={writing}
            onOpen={setOpenId}
            onChanged={() => setRev((r) => r + 1)}
            onDeleted={() => {
              setOpenId(null);
              setRev((r) => r + 1);
            }}
            onRevise={(id, change, title) => follow((cb) => studio.revise(id, change, cb), title)}
          />
        )}
      </section>
    </div>
  );
}

function familyOf(id: string, docs: StudioDoc[] | null): string {
  return docs?.find((d) => d.id === id)?.family_id ?? id;
}

function HistoryItem({ doc, on, onOpen }: { doc: StudioDoc; on: boolean; onOpen: () => void }) {
  return (
    <li>
      <button className={`studio-item ${on ? "is-on" : ""}`} onClick={onOpen}>
        <span className="studio-item-title">{doc.title}</span>
        <span className="studio-item-meta">
          <span className={`chip chip-s-${doc.status}`}>{STATUS_TEXT[doc.status] ?? doc.status}</span>
          {doc.template_name}
          {doc.versions > 1 && <> · v{doc.versions}</>}
        </span>
        <span className="studio-item-meta">
          {doc.created_by.name} · {when(doc.updated_at)}
        </span>
      </button>
    </li>
  );
}

// ---------------------------------------------------------------- composer

function Composer(props: {
  templates: StudioTemplate[];
  loading: boolean;
  busy: boolean;
  onWrite: (body: { template: string; title?: string; inputs: Record<string, string>; instructions: string }, title: string) => void;
}) {
  const [key, setKey] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [title, setTitle] = useState("");
  const [instructions, setInstructions] = useState("");
  const t = props.templates.find((x) => x.key === key) ?? null;
  const groups = useMemo(() => {
    const m = new Map<string, StudioTemplate[]>();
    props.templates.forEach((x) => m.set(x.category, [...(m.get(x.category) ?? []), x]));
    return [...m.entries()];
  }, [props.templates]);

  if (!t) {
    return (
      <div className="studio-pad">
        <h1 className="work-title">Drafting studio</h1>
        <p className="work-meta">
          Write a contract, legal notice, reply, affidavit, power of attorney, opinion or letter from your own facts. No
          matter or documents needed; anything you leave out becomes a [placeholder] to fill in.
        </p>
        {props.loading && <p className="muted">Loading document types…</p>}
        {groups.map(([category, list]) => (
          <div key={category} className="studio-group">
            <h2 className="studio-group-title">{category}</h2>
            <div className="studio-cards">
              {list.map((x) => (
                <button
                  key={x.key}
                  className="studio-card"
                  onClick={() => {
                    setKey(x.key);
                    setValues({});
                    setTitle("");
                    setInstructions("");
                  }}
                >
                  <span className="studio-card-name">{x.name}</span>
                  <span className="studio-card-desc">{x.description}</span>
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    );
  }

  const missing = t.fields.filter((f) => f.required && !(values[f.key] ?? "").trim());
  const ready = missing.length === 0 || instructions.trim().length > 0;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!ready || props.busy) return;
    const inputs = Object.fromEntries(Object.entries(values).filter(([, v]) => v.trim()));
    props.onWrite({ template: t.key, title: title.trim() || undefined, inputs, instructions }, title.trim() || t.name);
  };

  return (
    <form className="studio-pad studio-form" onSubmit={submit}>
      <button type="button" className="btn btn-quiet btn-small studio-back" onClick={() => setKey(null)}>
        ← All document types
      </button>
      <h1 className="work-title">{t.name}</h1>
      <p className="work-meta">{t.description}</p>
      <label className="field">
        <span>Title (optional)</span>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder={t.name} maxLength={300} />
      </label>
      <div className="studio-fields">
        {t.fields.map((f) => (
          <label key={f.key} className={`field ${f.long ? "studio-field-long" : ""}`}>
            <span>
              {f.label}
              {f.required && <em className="studio-req"> required</em>}
            </span>
            {f.long ? (
              <textarea
                rows={3}
                value={values[f.key] ?? ""}
                placeholder={f.hint}
                maxLength={6000}
                onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
              />
            ) : (
              <input
                value={values[f.key] ?? ""}
                placeholder={f.hint}
                maxLength={6000}
                onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
              />
            )}
          </label>
        ))}
      </div>
      <label className="field">
        <span>Instructions</span>
        <textarea
          rows={4}
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
          placeholder="Anything else: clauses to include or leave out, tone, length, special facts…"
          maxLength={8000}
        />
      </label>
      <div className="studio-actions">
        <button className="btn btn-primary" type="submit" disabled={!ready || props.busy}>
          {props.busy ? "Writing…" : "Write document"}
        </button>
        {!ready && (
          <span className="muted">
            Fill in {missing.map((f) => f.label).join(", ")}, or describe the document in the instructions.
          </span>
        )}
      </div>
    </form>
  );
}

// ---------------------------------------------------------------- document view

function DocView(props: {
  id: string | null;
  live: Live | null;
  who: Identity;
  rev: number;
  busy: boolean;
  onOpen: (id: string) => void;
  onChanged: () => void;
  onDeleted: () => void;
  onRevise: (id: string, change: string, title: string) => void;
}) {
  const doc = useLoad(props.id ? () => studio.document(props.id!) : null, [props.id, props.rev]);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [change, setChange] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const d: StudioDetail | null = doc.data && doc.data.id === props.id ? doc.data : null;
  const streaming = !!props.live && !props.live.error;
  const text = streaming || (props.live && !d) ? props.live!.text : (d?.content ?? "");
  const mayChange = !!d && (d.created_by.id === props.who.user_id || ["partner", "admin"].includes(props.who.role));

  const act = async (fn: () => Promise<unknown>, done?: string) => {
    setSaving(true);
    setMsg(null);
    try {
      await fn();
      if (done) setMsg(done);
    } catch (err) {
      setMsg(errorText(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="studio-pad studio-doc">
      <div className="studio-doc-head">
        <div className="studio-doc-titles">
          {d && mayChange && !streaming ? (
            <input
              className="studio-title-input"
              defaultValue={d.title}
              key={d.id + d.title}
              aria-label="Document title"
              onBlur={(e) => {
                const v = e.target.value.trim();
                if (v && v !== d.title) void act(() => studio.update(d.id, { title: v }).then(props.onChanged));
              }}
            />
          ) : (
            <h1 className="work-title">{d?.title ?? props.live?.title ?? "Document"}</h1>
          )}
          <p className="work-meta">
            {d ? (
              <>
                {d.template_name} · {d.created_by.name} · {when(d.created_at)}
                {d.model && <> · {d.model}</>}
                {d.output_tokens != null && <> · {d.output_tokens.toLocaleString()} tokens</>}
                {d.latency_ms != null && <> · {(d.latency_ms / 1000).toFixed(0)} s</>}
              </>
            ) : (
              "Starting…"
            )}
          </p>
        </div>
        <div className="studio-doc-actions">
          {d && !streaming && d.content.trim() && (
            <button className="btn" onClick={() => void act(() => studio.exportWord(d.id))} disabled={saving}>
              Download Word
            </button>
          )}
          {d && mayChange && !streaming && (
            <>
              {editing ? (
                <>
                  <button
                    className="btn btn-primary"
                    disabled={saving || draft === d.content}
                    onClick={() =>
                      void act(
                        () =>
                          studio.update(d.id, { content: draft }).then(() => {
                            setEditing(false);
                            props.onChanged();
                          }),
                        "Saved.",
                      )
                    }
                  >
                    Save edits
                  </button>
                  <button className="btn btn-quiet" onClick={() => setEditing(false)}>
                    Cancel
                  </button>
                </>
              ) : (
                <button
                  className="btn"
                  onClick={() => {
                    setDraft(d.content);
                    setEditing(true);
                  }}
                >
                  Edit text
                </button>
              )}
              <button
                className="btn btn-quiet btn-danger-text"
                disabled={saving}
                onClick={() => {
                  if (window.confirm(`Delete "${d.title}" and all its versions from the firm history?`))
                    void act(() => studio.remove(d.id).then(props.onDeleted));
                }}
              >
                Delete
              </button>
            </>
          )}
        </div>
      </div>

      {msg && <p className="note">{msg}</p>}
      {doc.error && !props.live && <ErrorNote>{doc.error}</ErrorNote>}
      {props.live?.notice && <p className="note">{props.live.notice}</p>}
      {props.live?.error && <ErrorNote>Writing stopped: {props.live.error}</ErrorNote>}
      {d?.status === "failed" && !props.live && d.error && <ErrorNote>{d.error}</ErrorNote>}
      {d?.status === "complete" && !props.live && d.error && <p className="note studio-warn">{d.error}</p>}

      {d && d.revisions.length > 1 && (
        <div className="studio-versions" aria-label="Versions">
          {d.revisions.map((r, i) => (
            <button
              key={r.id}
              className={`btn btn-small ${r.id === d.id ? "is-on" : ""}`}
              title={r.instructions || "First version"}
              onClick={() => props.onOpen(r.id)}
            >
              v{i + 1}
              {r.status !== "complete" && ` · ${STATUS_TEXT[r.status]}`}
            </button>
          ))}
          {d.parent_id && d.instructions && <span className="muted studio-change">Change asked: {d.instructions}</span>}
        </div>
      )}

      {editing && d ? (
        <textarea className="studio-editor" value={draft} onChange={(e) => setDraft(e.target.value)} spellCheck />
      ) : text ? (
        <Preview text={text} streaming={streaming} />
      ) : streaming ? (
        <p className="muted studio-waiting">{props.live?.notice ?? "Waiting for the model…"}</p>
      ) : doc.loading ? (
        <p className="muted">Loading…</p>
      ) : (
        <Empty title="No text">This version has no text.</Empty>
      )}

      {d && d.status === "complete" && !streaming && !editing && (
        <form
          className="studio-revise"
          onSubmit={(e) => {
            e.preventDefault();
            if (!change.trim() || props.busy) return;
            props.onRevise(d.id, change.trim(), d.title);
            setChange("");
          }}
        >
          <label className="field">
            <span>Ask for changes (writes a new version; this one is kept)</span>
            <textarea
              rows={2}
              value={change}
              onChange={(e) => setChange(e.target.value)}
              placeholder="e.g. Add an arbitration clause with a sole arbitrator seated at Delhi; make the notice period 30 days"
              maxLength={8000}
            />
          </label>
          <button className="btn btn-primary" type="submit" disabled={!change.trim() || props.busy}>
            Revise
          </button>
        </form>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- preview

const NOTES = /^\s*DRAFTING NOTES:?\s*$/i;

function Marked({ text }: { text: string }) {
  const parts = text.split(/(\[[^[\]\n]{1,120}\])/g);
  return (
    <>
      {parts.map((p, i) =>
        i % 2 === 1 ? (
          <mark key={i} className="studio-ph">
            {p}
          </mark>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </>
  );
}

function isHeading(line: string): boolean {
  const letters = line.replace(/[^A-Za-z]/g, "");
  return letters.length > 0 && line.length <= 90 && letters === letters.toUpperCase() && !line.endsWith(",");
}

/** Reads like the Word export: centred title, bold headings, indented sub-clauses, notes set apart. */
function Preview({ text, streaming }: { text: string; streaming: boolean }) {
  const lines = text.split("\n");
  const cut = lines.findIndex((l) => NOTES.test(l));
  const body = cut >= 0 ? lines.slice(0, cut) : lines;
  const notes = cut >= 0 ? lines.slice(cut + 1).map((l) => l.replace(/^\s*[-•*]\s*/, "").trim()).filter(Boolean) : [];
  let titled = false;
  return (
    <div className="studio-paper" aria-live={streaming ? "polite" : undefined}>
      {body.map((raw, i) => {
        const line = raw.trim();
        if (!line) return null;
        if (!titled) {
          titled = true;
          return (
            <p key={i} className="studio-p-title">
              <Marked text={line} />
            </p>
          );
        }
        const cls = isHeading(line)
          ? "studio-p-head"
          : /^\d+\.\d+/.test(line)
            ? "studio-p-sub"
            : /^(\([a-z]{1,3}\)|\([ivx]{1,6}\)|[-•] )/i.test(line)
              ? "studio-p-item"
              : "studio-p";
        return (
          <p key={i} className={cls}>
            <Marked text={line} />
          </p>
        );
      })}
      {streaming && <span className="studio-caret" aria-hidden="true" />}
      {notes.length > 0 && (
        <div className="studio-notes">
          <p className="studio-notes-title">Drafting notes (not part of the document)</p>
          <ul>
            {notes.map((n, i) => (
              <li key={i}>
                <Marked text={n} />
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
