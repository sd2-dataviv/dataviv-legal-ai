import { useState } from "react";
import { api, bytes, errorText, label, when } from "../api";
import { useLoad } from "../hooks";
import { Empty, ErrorNote, Highlighted, Id, StatusChip } from "./ui";

export type Selection =
  | { kind: "doc"; docId: string }
  | { kind: "chunk"; docId: string; chunkId: string; terms: string[] }
  | null;

export function Inspector(props: {
  selection: Selection;
  onInspect: (s: Selection) => void;
  onDeleted: () => void;
  docsRev: number;
}) {
  const s = props.selection;
  return (
    <aside className="inspector" aria-label="Inspector">
      {s && (
        <div className="inspector-bar">
          <button className="btn btn-small btn-quiet" onClick={() => props.onInspect(null)} aria-label="Close the inspector">
            Close ✕
          </button>
        </div>
      )}
      {!s && (
        <Empty title="Nothing selected">
          Pick a document to see its versions, or a search result to open the exact passage it cites.
        </Empty>
      )}
      {s?.kind === "doc" && <DocPanel docId={s.docId} rev={props.docsRev} onDeleted={props.onDeleted} />}
      {s?.kind === "chunk" && (
        <ChunkPanel
          docId={s.docId}
          chunkId={s.chunkId}
          terms={s.terms}
          onOpenDoc={() => props.onInspect({ kind: "doc", docId: s.docId })}
        />
      )}
    </aside>
  );
}

function DocPanel({ docId, rev, onDeleted }: { docId: string; rev: number; onDeleted: () => void }) {
  const doc = useLoad(() => api.document(docId), [docId, rev]);
  const [confirm, setConfirm] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const d = doc.data;

  async function remove() {
    setErr(null);
    try {
      await api.deleteDocument(docId);
      onDeleted();
    } catch (e) {
      setErr(errorText(e));
      setConfirm(false);
    }
  }

  if (doc.error) return <ErrorNote>{doc.error}</ErrorNote>;
  if (!d) return <p className="muted pad">Loading…</p>;
  return (
    <div className="panel">
      <h2 className="panel-title">{d.title}</h2>
      <StatusChip status={d.status} pending={d.ocr_pending_pages} />
      {d.error && <ErrorNote>{d.error}</ErrorNote>}
      <dl className="facts">
        <dt>Type</dt>
        <dd>{label(d.doc_type)}</dd>
        <dt>Pages</dt>
        <dd>{d.page_count ?? "–"}</dd>
        <dt>Passages</dt>
        <dd>{d.chunk_count}</dd>
        <dt>Added</dt>
        <dd>{when(d.created_at)}</dd>
        <dt>Document</dt>
        <dd>
          <Id value={d.id} />
        </dd>
      </dl>

      <h3>Versions</h3>
      <ol className="versions" reversed>
        {[...d.versions]
          .sort((a, b) => b.version_no - a.version_no)
          .map((v) => (
            <li key={v.id} className={v.id === d.current_version_id ? "is-current" : undefined}>
              <span className="v-no">v{v.version_no}</span>
              <span className="v-name">{v.original_filename}</span>
              <span className="muted">
                {bytes(v.size_bytes)} · {when(v.created_at)}
              </span>
              <code className="id" title={`SHA-256 ${v.sha256}`}>
                {v.sha256.slice(0, 12)}
              </code>
            </li>
          ))}
      </ol>

      {err && <ErrorNote>{err}</ErrorNote>}
      <div className="row">
        <button
          className="btn"
          onClick={() => api.download(d.id, d.versions.find((v) => v.id === d.current_version_id)?.original_filename ?? d.title).catch((e) => setErr(errorText(e)))}
        >
          Download current version
        </button>
        <span className="toolbar-gap" />
        {confirm ? (
          <>
            <button className="btn btn-quiet" onClick={() => setConfirm(false)}>
              Keep
            </button>
            <button className="btn btn-danger" onClick={remove}>
              Delete document
            </button>
          </>
        ) : (
          <button className="btn btn-quiet btn-danger-text" onClick={() => setConfirm(true)}>
            Delete…
          </button>
        )}
      </div>
    </div>
  );
}

function ChunkPanel(props: { docId: string; chunkId: string; terms: string[]; onOpenDoc: () => void }) {
  const chunk = useLoad(() => api.chunk(props.docId, props.chunkId), [props.docId, props.chunkId]);
  const c = chunk.data;
  if (chunk.error) return <ErrorNote>{chunk.error}</ErrorNote>;
  if (!c) return <p className="muted pad">Loading…</p>;
  return (
    <div className="panel">
      <p className="cite-label">Cited passage</p>
      <h2 className="panel-title">{c.section_path ?? `Passage ${c.ordinal + 1}`}</h2>
      <dl className="facts">
        <dt>Pages</dt>
        <dd>{c.page_from === c.page_to ? c.page_from : `${c.page_from}–${c.page_to}`}</dd>
        <dt>Characters</dt>
        <dd className="mono">
          {c.char_start.toLocaleString("en-GB")}–{c.char_end.toLocaleString("en-GB")}
        </dd>
        {c.ocr_confidence !== null && (
          <>
            <dt>OCR confidence</dt>
            <dd>{Math.round(c.ocr_confidence * 100)}%</dd>
          </>
        )}
        <dt>Passage</dt>
        <dd>
          <Id value={c.id} />
        </dd>
      </dl>
      <blockquote className="passage">
        <Highlighted text={c.text} terms={props.terms} />
      </blockquote>
      <button className="btn" onClick={props.onOpenDoc}>
        Open the document
      </button>
    </div>
  );
}
