import type { ReactNode } from "react";
import type { DocStatus } from "../api";

export function StatusChip({ status, pending }: { status: DocStatus | string; pending?: number[] }) {
  const text: Record<string, string> = {
    queued: "Queued",
    processing: "Processing",
    indexed: "Indexed",
    partial: "Partly indexed",
    failed: "Failed",
    running: "Running",
    succeeded: "Done",
  };
  const title = pending && pending.length ? `Waiting for OCR on page ${pending.join(", ")}` : undefined;
  return (
    <span className={`chip chip-${status}`} title={title}>
      {text[status] ?? status}
      {pending && pending.length > 0 && <span className="chip-extra"> · OCR p. {pending.join(", ")}</span>}
    </span>
  );
}

export function Id({ value }: { value: string }) {
  return (
    <code className="id" title={value}>
      {value.slice(0, 8)}
    </code>
  );
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <p className="note note-error" role="alert">
      {children}
    </p>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {children && <div className="empty-body">{children}</div>}
    </div>
  );
}

/** Turns the backend's «highlight» markers into <mark> without ever injecting HTML. */
export function Snippet({ text }: { text: string }) {
  const parts = text.split(/«([^»]*)»/g);
  return (
    <span className="snippet">
      {parts.map((p, i) => (i % 2 === 1 ? <mark key={i}>{p}</mark> : <span key={i}>{p}</span>))}
    </span>
  );
}

/** Highlights every occurrence of `terms` in `text` (case-insensitive). */
export function Highlighted({ text, terms }: { text: string; terms: string[] }) {
  const clean = terms.map((t) => t.trim()).filter((t) => t.length > 1);
  if (!clean.length) return <>{text}</>;
  const escaped = clean.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const re = new RegExp(`(${escaped.join("|")})`, "gi");
  const parts = text.split(re);
  return (
    <>
      {parts.map((p, i) => (i % 2 === 1 ? <mark key={i}>{p}</mark> : <span key={i}>{p}</span>))}
    </>
  );
}

export function snippetTerms(snippet: string): string[] {
  return [...snippet.matchAll(/«([^»]*)»/g)].map((m) => m[1]);
}
