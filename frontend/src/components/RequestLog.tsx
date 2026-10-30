import { useEffect, useState } from "react";
import { onRequest, type LogEntry } from "../api";

export function RequestLog() {
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [hideHealth, setHideHealth] = useState(true);

  useEffect(() => onRequest((e) => setEntries((prev) => [e, ...prev].slice(0, 300))), []);

  const isPoll = (e: LogEntry) => e.path === "/health" || e.path.endsWith("/presence");
  const shown = entries.filter((e) => !(hideHealth && isPoll(e)));
  const failures = entries.filter((e) => e.status === null || e.status >= 400).length;
  const last = shown[0];

  return (
    <section className={`log ${open ? "is-open" : ""}`} aria-label="Request log">
      <button className="log-bar" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="log-name">Requests</span>
        <span className="log-count">{entries.length}</span>
        {failures > 0 && <span className="log-fail">{failures} failed</span>}
        {last && !open && (
          <span className="log-last">
            <Status s={last.status} /> {last.method} {last.path} · {last.ms} ms
          </span>
        )}
        <span className="toolbar-gap" />
        <span className="log-toggle">{open ? "Hide" : "Show"}</span>
      </button>
      {open && (
        <div className="log-body">
          <div className="log-tools">
            <label>
              <input type="checkbox" checked={hideHealth} onChange={(e) => setHideHealth(e.target.checked)} /> Hide
              polling (health, presence)
            </label>
            <button className="btn btn-quiet btn-small" onClick={() => setEntries([])}>
              Clear
            </button>
          </div>
          <table className="log-table">
            <tbody>
              {shown.map((e) => (
                <FragmentRow
                  key={e.id}
                  e={e}
                  open={expanded === e.id}
                  onToggle={() => setExpanded(expanded === e.id ? null : e.id)}
                />
              ))}
            </tbody>
          </table>
          {shown.length === 0 && <p className="muted pad">No requests yet.</p>}
        </div>
      )}
    </section>
  );
}

function FragmentRow({ e, open, onToggle }: { e: LogEntry; open: boolean; onToggle: () => void }) {
  return (
    <>
      <tr className={e.detail ? "has-detail" : undefined} onClick={e.detail ? onToggle : undefined}>
        <td className="mono muted">{e.at.toLocaleTimeString("en-GB")}</td>
        <td>
          <Status s={e.status} />
        </td>
        <td className="mono">{e.method}</td>
        <td className="mono log-path">{e.path}</td>
        <td className="mono num">{e.ms} ms</td>
        <td className="muted">{e.who}</td>
      </tr>
      {open && e.detail && (
        <tr className="log-detail">
          <td colSpan={6}>
            <pre>{e.detail}</pre>
          </td>
        </tr>
      )}
    </>
  );
}

function Status({ s }: { s: number | null }) {
  const cls = s === null ? "bad" : s >= 500 ? "bad" : s >= 400 ? "warn" : "good";
  return <span className={`status status-${cls}`}>{s ?? "ERR"}</span>;
}
