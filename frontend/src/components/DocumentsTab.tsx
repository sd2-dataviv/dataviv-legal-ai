import { useState } from "react";
import { api, DOC_TYPES, label, when } from "../api";
import { useInterval, useLoad } from "../hooks";
import type { Selection } from "./Inspector";
import { Empty, ErrorNote, StatusChip } from "./ui";

const STATUSES = ["queued", "processing", "indexed", "partial", "failed"];

export function DocumentsTab(props: {
  matterId: string;
  rev: number;
  onInspect: (s: Selection) => void;
  selectedDoc: string | null;
}) {
  const [docType, setDocType] = useState("");
  const [status, setStatus] = useState("");
  const docs = useLoad(
    () => api.documents(props.matterId, { doc_type: docType || undefined, status: status || undefined }),
    [props.matterId, props.rev, docType, status],
  );
  const busy = !!docs.data?.some((d) => d.status === "queued" || d.status === "processing");
  useInterval(docs.reload, 2000, busy); // keep statuses live while ingestion runs

  return (
    <section>
      <div className="toolbar">
        <label className="field field-inline">
          <span>Type</span>
          <select value={docType} onChange={(e) => setDocType(e.target.value)}>
            <option value="">All types</option>
            {DOC_TYPES.map((t) => (
              <option key={t} value={t}>
                {label(t)}
              </option>
            ))}
          </select>
        </label>
        <label className="field field-inline">
          <span>Status</span>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Any status</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {label(s)}
              </option>
            ))}
          </select>
        </label>
        <span className="toolbar-gap" />
        {busy && <span className="live">Refreshing while documents process</span>}
        <button className="btn btn-quiet" onClick={docs.reload}>
          Refresh
        </button>
      </div>

      {docs.error && <ErrorNote>{docs.error}</ErrorNote>}
      {docs.data && docs.data.length === 0 && (
        <Empty title={docType || status ? "No documents match these filters" : "No documents yet"}>
          {docType || status ? "Clear a filter to see more." : "Use the Upload tab to add PDFs, Word files, emails or scans."}
        </Empty>
      )}
      {docs.data && docs.data.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Document</th>
              <th>Type</th>
              <th>Status</th>
              <th className="num">Pages</th>
              <th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {docs.data.map((d) => (
              <tr
                key={d.id}
                className={props.selectedDoc === d.id ? "is-on" : undefined}
                onClick={() => props.onInspect({ kind: "doc", docId: d.id })}
                tabIndex={0}
                onKeyDown={(e) => e.key === "Enter" && props.onInspect({ kind: "doc", docId: d.id })}
              >
                <td className="cell-title">
                  {d.title}
                  {d.error && <small className="cell-error">{d.error}</small>}
                </td>
                <td>{label(d.doc_type)}</td>
                <td>
                  <StatusChip status={d.status} pending={d.ocr_pending_pages} />
                </td>
                <td className="num">{d.page_count ?? "–"}</td>
                <td className="nowrap">{when(d.updated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
