import { useEffect, useState, type FormEvent } from "react";
import {
  api,
  errorText,
  label,
  when,
  type AnswerType,
  type ColumnInput,
  type ReviewCell,
  type ReviewColumn,
  type ReviewDetail,
} from "../api";
import { useInterval, useLoad } from "../hooks";
import type { Selection } from "./Inspector";
import { Empty, ErrorNote } from "./ui";

const TYPES: { id: AnswerType; name: string }[] = [
  { id: "text", name: "Text" },
  { id: "date", name: "Date" },
  { id: "amount", name: "Amount (₹)" },
  { id: "yes_no", name: "Yes / no" },
  { id: "choice", name: "Choice" },
];

export function ReviewTab(props: { matterId: string; onInspect: (s: Selection) => void }) {
  const [tableId, setTableId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [rev, setRev] = useState(0);
  const list = useLoad(() => api.reviewTables(props.matterId), [props.matterId, rev]);

  return (
    <section className="reviews">
      <aside className="threads" aria-label="Review tables">
        <button className="btn btn-primary threads-new" onClick={() => setCreating(true)}>
          New review table
        </button>
        {list.error && <ErrorNote>{list.error}</ErrorNote>}
        {list.data?.length === 0 && <p className="muted threads-empty">No review tables yet.</p>}
        <ul>
          {list.data?.map((t) => (
            <li key={t.id}>
              <button
                className={`thread ${tableId === t.id && !creating ? "is-on" : ""}`}
                onClick={() => {
                  setCreating(false);
                  setTableId(t.id);
                }}
              >
                <span className="thread-title">{t.title}</span>
                <span className="thread-meta">
                  {t.documents} documents × {t.columns} questions
                  {t.job && (t.job.status === "running" || t.job.status === "queued") ? ` · reading ${t.job.progress}%` : ""}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </aside>
      {creating ? (
        <CreateTable
          matterId={props.matterId}
          onCancel={() => setCreating(false)}
          onCreated={(id) => {
            setCreating(false);
            setTableId(id);
            setRev((r) => r + 1);
          }}
        />
      ) : tableId ? (
        <Grid key={tableId} tableId={tableId} matterId={props.matterId} onInspect={props.onInspect} onChanged={() => setRev((r) => r + 1)} onDeleted={() => { setTableId(null); setRev((r) => r + 1); }} />
      ) : (
        <Empty title="Ask the same questions of many documents">
          A review table puts each document in a row and each question in a column. Every answer quotes the passage it
          came from, and answers whose quote or figures don't match the document are flagged.
        </Empty>
      )}
    </section>
  );
}

function CreateTable(props: { matterId: string; onCancel: () => void; onCreated: (id: string) => void }) {
  const templates = useLoad(() => api.reviewTemplates(), []);
  const docs = useLoad(() => api.documents(props.matterId), [props.matterId]);
  const [template, setTemplate] = useState<string>("contracts");
  const [title, setTitle] = useState("");
  const [columns, setColumns] = useState<ColumnInput[]>([]);
  const [which, setWhich] = useState<"all" | "some">("all");
  const [chosen, setChosen] = useState<string[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const usable = (docs.data ?? []).filter((d) => d.status === "indexed" || d.status === "partial");
  const t = templates.data?.find((x) => x.id === template);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const d = await api.createReview(props.matterId, {
        title: title.trim() || undefined,
        template: template === "blank" ? null : template,
        columns,
        document_ids: which === "all" ? null : chosen,
      });
      props.onCreated(d.id);
    } catch (e2) {
      setErr(errorText(e2));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="review-create" onSubmit={submit}>
      <h2>New review table</h2>
      <div className="tpl-list" role="radiogroup" aria-label="Start from">
        {[...(templates.data ?? []), { id: "blank", title: "Blank", description: "Write your own questions", columns: [] }].map((x) => (
          <button
            key={x.id}
            type="button"
            role="radio"
            aria-checked={template === x.id}
            className={`tpl ${template === x.id ? "is-on" : ""}`}
            onClick={() => setTemplate(x.id)}
          >
            <strong>{x.title}</strong>
            <span>{x.description}</span>
            {x.columns.length > 0 && <span className="muted">{x.columns.map((c) => c.label).join(", ")}</span>}
          </button>
        ))}
      </div>
      <label className="field">
        <span>Title</span>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder={t?.title ?? "Review table"} />
      </label>
      <fieldset className="field">
        <span>Documents</span>
        <label className="radio">
          <input type="radio" checked={which === "all"} onChange={() => setWhich("all")} /> Every indexed document in the matter ({usable.length})
        </label>
        <label className="radio">
          <input type="radio" checked={which === "some"} onChange={() => setWhich("some")} /> Choose documents
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
      <div className="field">
        <span>{template === "blank" ? "Questions" : "Extra questions (optional)"}</span>
        {columns.map((c, i) => (
          <div key={i} className="col-chip">
            <strong>{c.label}</strong> <span className="muted">({label(c.answer_type)})</span> {c.question}
            <button type="button" className="link" onClick={() => setColumns(columns.filter((_, j) => j !== i))}>
              remove
            </button>
          </div>
        ))}
        <ColumnForm onAdd={(c) => setColumns([...columns, c])} submitLabel="Add question" />
      </div>
      {err && <ErrorNote>{err}</ErrorNote>}
      <div className="row">
        <button className="btn btn-primary" disabled={busy || (template === "blank" && columns.length === 0) || (which === "some" && chosen.length === 0)}>
          {busy ? "Creating…" : "Create table"}
        </button>
        <button type="button" className="btn btn-quiet" onClick={props.onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function ColumnForm(props: { onAdd: (c: ColumnInput) => void | Promise<void>; submitLabel: string; initial?: ReviewColumn }) {
  const [c, setC] = useState<ColumnInput>(
    props.initial
      ? { label: props.initial.label, question: props.initial.question, answer_type: props.initial.answer_type, choices: props.initial.choices }
      : { label: "", question: "", answer_type: "text", choices: [] },
  );
  const [choices, setChoices] = useState(c.choices.join(", "));
  const ok = c.label.trim() && c.question.trim().length >= 5 && (c.answer_type !== "choice" || choices.trim());
  const add = async () => {
    if (!ok) return;
    await props.onAdd({ ...c, choices: c.answer_type === "choice" ? choices.split(",").map((x) => x.trim()).filter(Boolean) : [] });
    if (!props.initial) {
      setC({ label: "", question: "", answer_type: "text", choices: [] });
      setChoices("");
    }
  };
  return (
    <div className="col-form">
      <input value={c.label} onChange={(e) => setC({ ...c, label: e.target.value })} placeholder="Column name, e.g. Notice period" aria-label="Column name" />
      <input
        className="grow"
        value={c.question}
        onChange={(e) => setC({ ...c, question: e.target.value })}
        placeholder="Question, e.g. How much notice is needed to terminate?"
        aria-label="Question"
      />
      <select value={c.answer_type} onChange={(e) => setC({ ...c, answer_type: e.target.value as AnswerType })} aria-label="Answer type">
        {TYPES.map((t) => (
          <option key={t.id} value={t.id}>
            {t.name}
          </option>
        ))}
      </select>
      {c.answer_type === "choice" && (
        <input value={choices} onChange={(e) => setChoices(e.target.value)} placeholder="Options, comma separated" aria-label="Choices" />
      )}
      <button type="button" className="btn btn-small" disabled={!ok} onClick={add}>
        {props.submitLabel}
      </button>
    </div>
  );
}

function Grid(props: {
  tableId: string;
  matterId: string;
  onInspect: (s: Selection) => void;
  onChanged: () => void;
  onDeleted: () => void;
}) {
  const [t, setT] = useState<ReviewDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null); // cell id
  const [addingCol, setAddingCol] = useState(false);
  const [editCol, setEditCol] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);

  const load = async () => {
    try {
      const d = await api.review(props.tableId);
      setT(d);
      setTitle((cur) => cur || d.title);
      setErr(null);
    } catch (e) {
      setErr(errorText(e));
    }
  };
  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.tableId]);
  const running = !!t?.job && (t.job.status === "queued" || t.job.status === "running");
  useInterval(() => void load(), 2500, running); // cells fill in live while the job reads documents
  useEffect(() => {
    if (t && !running) props.onChanged();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);

  async function act(fn: () => Promise<ReviewDetail | unknown>) {
    setErr(null);
    try {
      const out = await fn();
      if (out && typeof out === "object" && "cells" in out) setT(out as ReviewDetail);
      else await load();
    } catch (e) {
      setErr(errorText(e));
    }
  }

  if (!t) return err ? <ErrorNote>{err}</ErrorNote> : <p className="muted pad">Loading…</p>;
  const grid = new Map(t.cells.map((c) => [`${c.document_id}:${c.column_id}`, c]));
  const opened = open ? t.cells.find((c) => c.id === open) ?? null : null;
  const tot = t.totals;

  return (
    <div className="review">
      <div className="review-head">
        <input
          className="editor-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          onBlur={() => title.trim() && title !== t.title && act(() => api.renameReview(t.id, title.trim()))}
          disabled={!t.can_edit}
          aria-label="Table title"
        />
        <div className="row">
          {t.can_edit &&
            (running ? (
              <button className="btn" onClick={() => act(() => api.cancelReview(t.id))}>
                Stop reading
              </button>
            ) : (
              <>
                <button className="btn btn-primary" onClick={() => act(() => api.runReview(t.id, "missing"))} disabled={t.columns === 0}>
                  {tot.done + tot.not_found === 0 ? "Fill in the table" : "Fill in empty and failed cells"}
                </button>
                {tot.done + tot.not_found > 0 && (
                  <button className="btn" onClick={() => act(() => api.runReview(t.id, "all"))}>
                    Re-read everything
                  </button>
                )}
              </>
            ))}
          {t.can_edit && !running && (
            <button className="btn btn-quiet" onClick={() => setAddingCol(!addingCol)} aria-expanded={addingCol}>
              Add question
            </button>
          )}
          <span className="toolbar-gap" />
          <button className="btn btn-small" onClick={() => api.exportReview(t.id, "xlsx").catch((e) => setErr(errorText(e)))}>
            Export to Excel
          </button>
          <button className="btn btn-small btn-quiet" onClick={() => api.exportReview(t.id, "csv").catch((e) => setErr(errorText(e)))}>
            CSV
          </button>
          {t.can_edit && !running && (
            confirmDelete ? (
              <>
                <button
                  className="btn btn-small btn-danger"
                  onClick={() => api.deleteReview(t.id).then(props.onDeleted).catch((e) => setErr(errorText(e)))}
                >
                  Delete table
                </button>
                <button className="btn btn-small btn-quiet" onClick={() => setConfirmDelete(false)}>
                  Keep
                </button>
              </>
            ) : (
              <button className="btn btn-small btn-quiet btn-danger-text" onClick={() => setConfirmDelete(true)}>
                Delete…
              </button>
            )
          )}
        </div>
      </div>

      {t.job && (running || t.job.status === "failed" || t.job.status === "cancelled") && (
        <div className="card job">
          <div className="row">
            <strong>{running ? "Reading documents" : t.job.status === "cancelled" ? "Stopped" : "Finished with problems"}</strong>
            <span className="muted">{t.job.current_step}</span>
          </div>
          <div className="bar" role="progressbar" aria-valuenow={t.job.progress} aria-valuemin={0} aria-valuemax={100}>
            <div className={`bar-fill bar-${t.job.status}`} style={{ width: `${t.job.progress}%` }} />
          </div>
          <p className="muted">
            {t.job.done} of {t.job.total} documents
            {t.job.error ? ` · ${t.job.error}` : ""}
          </p>
        </div>
      )}
      {addingCol && (
        <div className="card">
          <ColumnForm submitLabel="Add question" onAdd={(c) => act(() => api.addColumn(t.id, c)).then(() => setAddingCol(false))} />
        </div>
      )}
      {err && <ErrorNote>{err}</ErrorNote>}

      <p className="review-totals">
        <span className="v v-supported">{tot.done} answered</span>
        {tot.not_found > 0 && <span className="v">{tot.not_found} not stated</span>}
        {tot.unverified > 0 && <span className="v v-partial">{tot.unverified} to check</span>}
        {tot.failed > 0 && <span className="v v-unsupported">{tot.failed} failed</span>}
        {tot.reviewed > 0 && <span className="muted"> · {tot.reviewed} reviewed by a person</span>}
        {tot.edited > 0 && <span className="muted"> · {tot.edited} corrected</span>}
        {tot.input_tokens > 0 && (
          <span className="ans-meta">
            {" "}
            · {tot.input_tokens.toLocaleString("en-GB")} in / {tot.output_tokens.toLocaleString("en-GB")} out · ${tot.cost_usd.toFixed(4)}
          </span>
        )}
      </p>

      <div className="review-body">
        <div className="grid-wrap">
          {t.rows.length === 0 ? (
            <Empty title="No documents in this table">Create a new table once documents have finished indexing.</Empty>
          ) : (
            <table className="rgrid">
              <thead>
                <tr>
                  <th className="rgrid-doc">Document</th>
                  {t.column_list.map((c) => (
                    <th key={c.id} title={c.question}>
                      <span className="col-label">{c.label}</span>
                      <span className="col-type">{TYPES.find((x) => x.id === c.answer_type)?.name}</span>
                      {t.can_edit && !running && (
                        <button className="link col-edit" onClick={() => setEditCol(editCol === c.id ? null : c.id)}>
                          edit
                        </button>
                      )}
                      {editCol === c.id && (
                        <div className="col-pop card">
                          <ColumnForm
                            initial={c}
                            submitLabel="Save (clears this column)"
                            onAdd={(x) => act(() => api.patchColumn(t.id, c.id, x)).then(() => setEditCol(null))}
                          />
                          <button className="link btn-danger-text" onClick={() => act(() => api.deleteColumn(t.id, c.id)).then(() => setEditCol(null))}>
                            Delete this column
                          </button>
                        </div>
                      )}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {t.rows.map((r) => (
                  <tr key={r.document_id}>
                    <th className="rgrid-doc" scope="row">
                      <span className="doc-name">{r.title}</span>
                      <span className="muted">{label(r.doc_type)}</span>
                    </th>
                    {t.column_list.map((c) => {
                      const cell = grid.get(`${r.document_id}:${c.id}`);
                      return (
                        <td key={c.id} className={cell?.id === open ? "is-on" : undefined}>
                          <CellView cell={cell} onOpen={() => cell && setOpen(cell.id)} />
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        {opened && (
          <CellPanel
            key={opened.id}
            cell={opened}
            column={t.column_list.find((c) => c.id === opened.column_id)!}
            doc={t.rows.find((r) => r.document_id === opened.document_id)?.title ?? ""}
            canEdit={t.can_edit}
            onClose={() => setOpen(null)}
            onInspect={props.onInspect}
            act={(fn) => act(fn)}
            tableId={t.id}
          />
        )}
      </div>
      <p className="muted review-foot">
        Created by {t.created_by.name} · updated {when(t.updated_at)}
      </p>
    </div>
  );
}

function CellView({ cell, onOpen }: { cell: ReviewCell | undefined; onOpen: () => void }) {
  if (!cell || cell.status === "empty") return <span className="cell-empty">–</span>;
  if (cell.status === "queued") return <span className="cell-wait">Reading…</span>;
  const mark =
    cell.status === "failed"
      ? { cls: "m-fail", text: "failed" }
      : cell.status === "not_found"
        ? { cls: "m-none", text: "" }
        : cell.edited_by
          ? { cls: "m-edit", text: "corrected" }
          : cell.verified
            ? { cls: "m-ok", text: "" }
            : { cls: "m-check", text: "check" };
  return (
    <button className={`cellbtn ${mark.cls}`} onClick={onOpen} title={cell.flag ?? cell.error ?? undefined}>
      <span className="cell-text">
        {cell.status === "failed" ? cell.error ?? "Failed" : cell.status === "not_found" ? "Not stated" : cell.answer}
      </span>
      <span className="cell-meta">
        {cell.page_from !== null && <span>p. {cell.page_from}</span>}
        {mark.text && <span className="cell-mark">{mark.text}</span>}
        {cell.reviewed_by && <span className="cell-reviewed" title={`Reviewed by ${cell.reviewed_by.name}`}>✓ reviewed</span>}
      </span>
    </button>
  );
}

function CellPanel(props: {
  cell: ReviewCell;
  column: ReviewColumn;
  doc: string;
  canEdit: boolean;
  tableId: string;
  onClose: () => void;
  onInspect: (s: Selection) => void;
  act: (fn: () => Promise<unknown>) => Promise<void>;
}) {
  const { cell, column } = props;
  const [draft, setDraft] = useState(cell.answer ?? "");
  return (
    <aside className="cell-panel" aria-label="Answer details">
      <div className="row">
        <strong>{column.label}</strong>
        <span className="toolbar-gap" />
        <button className="btn btn-small btn-quiet" onClick={props.onClose}>
          Close
        </button>
      </div>
      <p className="muted">{column.question}</p>
      <p className="cell-doc">{props.doc}</p>
      {cell.status === "done" && (
        <>
          <p className="cell-answer">{cell.answer}</p>
          {cell.value && column.answer_type !== "text" && (
            <p className="muted">
              Normalised value: <code>{cell.value}</code>
            </p>
          )}
          {cell.quote && (
            <blockquote className="passage">
              <mark>{cell.quote}</mark>
            </blockquote>
          )}
          <p className={cell.verified ? "note note-ok" : "note note-warn"}>
            {cell.verified ? "The quote appears word for word in the document and every figure matches." : cell.flag ?? "Not verified."}
          </p>
          {cell.edited_by && cell.model_answer !== cell.answer && (
            <p className="muted">
              Corrected by {cell.edited_by.name}. The model said: “{cell.model_answer}”
            </p>
          )}
          {cell.chunk_id && (
            <button
              className="btn btn-small"
              onClick={() =>
                props.onInspect({
                  kind: "chunk",
                  docId: cell.document_id,
                  chunkId: cell.chunk_id!,
                  terms: (cell.quote ?? "").split(/\s+/).filter((w) => w.length > 3).slice(0, 12),
                })
              }
            >
              Open the passage (p. {cell.page_from})
            </button>
          )}
        </>
      )}
      {cell.status === "not_found" && <p className="note">The document does not say. {cell.flag ?? ""}</p>}
      {cell.status === "failed" && <ErrorNote>{cell.error ?? "Failed"}</ErrorNote>}
      {props.canEdit && (
        <div className="cell-edit">
          <label className="field">
            <span>Correct the answer</span>
            <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={3} />
          </label>
          <div className="row">
            <button
              className="btn btn-small btn-primary"
              disabled={!draft.trim() || draft === cell.answer}
              onClick={() => props.act(() => api.patchCell(props.tableId, cell.id, { answer: draft }))}
            >
              Save correction
            </button>
            {cell.edited_by && (
              <button className="btn btn-small btn-quiet" onClick={() => props.act(() => api.patchCell(props.tableId, cell.id, { revert: true }))}>
                Use the model's answer
              </button>
            )}
            <span className="toolbar-gap" />
            <label className="radio">
              <input
                type="checkbox"
                checked={!!cell.reviewed_by}
                onChange={(e) => props.act(() => api.patchCell(props.tableId, cell.id, { reviewed: e.target.checked }))}
              />{" "}
              Reviewed
            </label>
          </div>
        </div>
      )}
    </aside>
  );
}
