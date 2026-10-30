import { useEffect, useState, type FormEvent } from "react";
import {
  api,
  DOC_TYPES,
  errorText,
  label,
  type SearchHit,
  type SearchMode,
  type SearchResponse,
  type SearchStatus,
} from "../api";
import type { Selection } from "./Inspector";
import { Empty, ErrorNote, Snippet, snippetTerms } from "./ui";

const MODES: { value: SearchMode; text: string; hint: string }[] = [
  { value: "hybrid", text: "Words + meaning", hint: "Exact words and passages that mean the same thing" },
  { value: "keyword", text: "Exact words", hint: "Only passages containing your words" },
  { value: "semantic", text: "Meaning only", hint: "Passages close in meaning, even with no words in common" },
];

const MATCH_TEXT: Record<SearchHit["match"], string> = {
  keyword: "words",
  semantic: "meaning",
  both: "words + meaning",
};

function StatusLine({ status }: { status: SearchStatus | null }) {
  if (!status) return null;
  if (!status.enabled)
    return <p className="search-status">Semantic search is off: results are exact-word matches.</p>;
  if (status.reachable === false)
    return (
      <p className="search-status is-warn">
        Semantic search is configured but unreachable ({status.error}). Results fall back to exact words.
      </p>
    );
  return (
    <p className="search-status">
      Meaning by <b>{status.model}</b> · {status.vectors ?? 0} passages indexed
      {status.pending_passages ? ` · ${status.pending_passages} waiting (run scripts/reindex_vectors.py)` : ""}
    </p>
  );
}

export function SearchTab(props: {
  matterId: string | null;
  matterTitle: string | null;
  onInspect: (s: Selection) => void;
  selectedChunk: string | null;
}) {
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<"matter" | "all">(props.matterId ? "matter" : "all");
  const [types, setTypes] = useState<string[]>([]);
  const [limit, setLimit] = useState(10);
  const [res, setRes] = useState<SearchResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<SearchMode>("hybrid");
  const [status, setStatus] = useState<SearchStatus | null>(null);
  const effectiveScope = props.matterId ? scope : "all";

  useEffect(() => {
    api.searchStatus().then(setStatus, () => setStatus(null));
  }, [res]);

  async function run(e?: FormEvent) {
    e?.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      setRes(
        await api.search({
          query,
          matter_ids: effectiveScope === "matter" && props.matterId ? [props.matterId] : null,
          doc_types: types.length ? types : null,
          limit,
          mode,
        }),
      );
    } catch (e2) {
      setErr(errorText(e2));
    } finally {
      setBusy(false);
    }
  }

  const toggle = (t: string) => setTypes(types.includes(t) ? types.filter((x) => x !== t) : [...types, t]);

  return (
    <section className="search">
      <form className="search-bar" onSubmit={run}>
        <input
          className="search-input"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="termination for convenience, जमानत, Section 439…"
          aria-label="Search query"
          autoFocus
        />
        <button className="btn btn-primary" disabled={busy || query.trim().length < 2}>
          {busy ? "Searching…" : "Search"}
        </button>
      </form>

      <div className="filters">
        <div className="seg" role="radiogroup" aria-label="Match by">
          {MODES.map((m) => (
            <button
              key={m.value}
              type="button"
              role="radio"
              aria-checked={mode === m.value}
              className={mode === m.value ? "is-on" : ""}
              onClick={() => setMode(m.value)}
              title={m.hint}
            >
              {m.text}
            </button>
          ))}
        </div>
        <div className="seg" role="radiogroup" aria-label="Scope">
          <button
            type="button"
            role="radio"
            aria-checked={effectiveScope === "matter"}
            className={effectiveScope === "matter" ? "is-on" : ""}
            disabled={!props.matterId}
            onClick={() => setScope("matter")}
            title={props.matterTitle ?? "Select a matter first"}
          >
            This matter
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={effectiveScope === "all"}
            className={effectiveScope === "all" ? "is-on" : ""}
            onClick={() => setScope("all")}
          >
            All my matters
          </button>
        </div>
        <div className="type-chips" aria-label="Document types">
          {DOC_TYPES.map((t) => (
            <button
              key={t}
              type="button"
              className={`pick ${types.includes(t) ? "is-on" : ""}`}
              aria-pressed={types.includes(t)}
              onClick={() => toggle(t)}
            >
              {label(t)}
            </button>
          ))}
        </div>
        <label className="field field-inline">
          <span>Show</span>
          <select value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
            {[5, 10, 25, 50].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
      </div>

      <StatusLine status={status} />
      {err && <ErrorNote>{err}</ErrorNote>}
      {res?.notice && <p className="search-status is-warn">{res.notice}</p>}
      {res && res.hits.length === 0 && (
        <Empty title={`Nothing matches “${res.query}”`}>
          Try fewer words, widen the scope, or clear the type filters. Only indexed text is searched.
        </Empty>
      )}
      {res && res.hits.length > 0 && (
        <ol className="hits">
          {res.hits.map((h) => (
            <li key={h.chunk_id}>
              <button
                className={`hit ${props.selectedChunk === h.chunk_id ? "is-on" : ""}`}
                onClick={() =>
                  props.onInspect({
                    kind: "chunk",
                    docId: h.document_id,
                    chunkId: h.chunk_id,
                    terms: snippetTerms(h.snippet),
                  })
                }
              >
                <span className="hit-head">
                  <span className="hit-title">{h.document_title}</span>
                  <span className="hit-where">
                    {h.page_from === h.page_to ? `p. ${h.page_from}` : `pp. ${h.page_from}–${h.page_to}`}
                  </span>
                </span>
                {h.section_path && <span className="hit-section">{h.section_path}</span>}
                <Snippet text={h.snippet} />
                <span className="hit-foot">
                  <span className={`chip chip-match-${h.match}`}>{MATCH_TEXT[h.match]}</span>{" "}
                  {label(h.doc_type)}
                  {h.similarity !== null && ` · ${Math.round(h.similarity * 100)}% similar`} · score{" "}
                  {h.score.toFixed(3)}
                </span>
              </button>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
