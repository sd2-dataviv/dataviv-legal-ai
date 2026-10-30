import { useRef, useState, type DragEvent } from "react";
import { api, bytes, errorText, label, type Job, type UploadResult } from "../api";
import type { Selection } from "./Inspector";
import { ErrorNote, StatusChip } from "./ui";

const ACCEPT = ".pdf,.docx,.txt,.md,.eml,.msg,.png,.jpg,.jpeg,.tif,.tiff";

export function UploadTab(props: { matterId: string; onDone: () => void; onInspect: (s: Selection) => void }) {
  const [files, setFiles] = useState<File[]>([]);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<UploadResult | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const add = (list: FileList | null) => {
    if (!list) return;
    setFiles((prev) => {
      const seen = new Set(prev.map((f) => `${f.name}:${f.size}`));
      return [...prev, ...[...list].filter((f) => !seen.has(`${f.name}:${f.size}`))];
    });
  };

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setOver(false);
    add(e.dataTransfer.files);
  }

  async function poll(jobId: string) {
    for (;;) {
      let j: Job;
      try {
        j = await api.job(jobId);
      } catch (e) {
        setErr(errorText(e));
        return;
      }
      setJob(j);
      if (j.status === "succeeded" || j.status === "failed") {
        props.onDone();
        return;
      }
      await new Promise((r) => setTimeout(r, 1000));
    }
  }

  async function upload() {
    setBusy(true);
    setErr(null);
    setResult(null);
    setJob(null);
    try {
      const r = await api.upload(props.matterId, files);
      setResult(r);
      setFiles([]);
      props.onDone();
      if (r.job_id) await poll(r.job_id);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const total = files.reduce((n, f) => n + f.size, 0);

  return (
    <section className="upload">
      <div
        className={`drop ${over ? "is-over" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
        onClick={() => input.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
      >
        <p className="drop-title">Drop files here, or click to choose</p>
        <p className="drop-sub">PDF, Word, email, text and scanned images. Up to 50 MB each, 500 per upload.</p>
        <input
          ref={input}
          type="file"
          multiple
          accept={ACCEPT}
          hidden
          onChange={(e) => {
            add(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      {files.length > 0 && (
        <div className="card">
          <ul className="file-list">
            {files.map((f) => (
              <li key={`${f.name}:${f.size}`}>
                <span>{f.name}</span>
                <span className="muted">{bytes(f.size)}</span>
                <button
                  className="btn btn-quiet btn-small"
                  onClick={() => setFiles(files.filter((x) => x !== f))}
                  aria-label={`Remove ${f.name}`}
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
          <div className="row">
            <span className="muted">
              {files.length} file{files.length === 1 ? "" : "s"}, {bytes(total)}
            </span>
            <span className="toolbar-gap" />
            <button className="btn btn-quiet" onClick={() => setFiles([])} disabled={busy}>
              Clear
            </button>
            <button className="btn btn-primary" onClick={upload} disabled={busy}>
              {busy ? "Uploading…" : `Upload ${files.length} file${files.length === 1 ? "" : "s"}`}
            </button>
          </div>
        </div>
      )}

      {err && <ErrorNote>{err}</ErrorNote>}

      {job && (
        <div className="card job">
          <div className="row">
            <strong>Ingestion job</strong>
            <code className="id">{job.id.slice(0, 8)}</code>
            <span className="toolbar-gap" />
            <StatusChip status={job.status} />
          </div>
          <div
            className="bar"
            role="progressbar"
            aria-valuenow={job.progress}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div className={`bar-fill bar-${job.status}`} style={{ width: `${job.progress}%` }} />
          </div>
          <p className="muted">
            {job.done} of {job.total} processed{job.current_step ? ` · ${job.current_step}` : ""}
          </p>
          {job.error && <ErrorNote>{job.error}</ErrorNote>}
        </div>
      )}

      {result && (
        <table className="table">
          <thead>
            <tr>
              <th>File</th>
              <th>Outcome</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {result.files.map((f, i) => (
              <tr
                key={i}
                onClick={() => f.document_id && props.onInspect({ kind: "doc", docId: f.document_id })}
                className={f.document_id ? undefined : "is-static"}
              >
                <td className="cell-title">{f.filename}</td>
                <td>
                  <span className={`chip chip-up-${f.status}`}>{label(f.status)}</span>
                </td>
                <td className="muted">
                  {f.reason ?? (f.version_no ? `Version ${f.version_no}` : "")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
