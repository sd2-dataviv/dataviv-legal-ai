import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import {
  api,
  errorText,
  label,
  type AnswerSentence,
  type AnswerSource,
  type AskBody,
  type ChatMessage,
  type DraftKind,
  type Mode,
} from "../api";
import { useLoad } from "../hooks";
import type { Selection } from "./Inspector";
import { Empty, ErrorNote } from "./ui";

const MODES: { id: Mode; name: string; hint: string }[] = [
  { id: "ask", name: "Ask", hint: "Answer a question from the matter's documents" },
  { id: "summarise", name: "Summarise", hint: "Summarise chosen documents, or the whole matter" },
  { id: "draft", name: "Draft", hint: "Draft a notice, reply, email or memo from the documents" },
];
const DRAFTS: { id: DraftKind; name: string }[] = [
  { id: "notice", name: "Legal notice" },
  { id: "reply", name: "Reply to notice" },
  { id: "email", name: "Client email" },
  { id: "memo", name: "Internal memo" },
];
const PLACEHOLDER: Record<Mode, string> = {
  ask: "What is the notice period for termination?",
  summarise: "Anything to focus on? (optional, e.g. deadlines and amounts)",
  draft: "Reply refusing the payment demand and reserving our client's rights",
};

type Live = {
  question: string;
  mode: Mode;
  text: string;
  sources: AnswerSource[];
  notice: string | null;
  error: string | null;
};

export function AssistantTab(props: {
  matterId: string;
  onInspect: (s: Selection) => void;
  onOpenDraft: (draftId: string) => void;
}) {
  const [threadId, setThreadId] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [rev, setRev] = useState(0);
  const threads = useLoad(() => api.threads(props.matterId, query || undefined), [props.matterId, query, rev]);
  const models = useLoad(() => api.models(), []);

  useEffect(() => {
    const id = setTimeout(() => setQuery(q.trim()), 300);
    return () => clearTimeout(id);
  }, [q]);

  return (
    <section className="assistant">
      <aside className="threads" aria-label="Threads">
        <button className="btn btn-primary threads-new" onClick={() => setThreadId(null)}>
          New thread
        </button>
        <input
          className="threads-search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search threads"
          aria-label="Search threads and answers"
        />
        {threads.error && <ErrorNote>{threads.error}</ErrorNote>}
        {threads.data && threads.data.length === 0 && (
          <p className="muted threads-empty">{query ? "No thread mentions that." : "No threads yet."}</p>
        )}
        <ul>
          {threads.data?.map((t) => (
            <li key={t.id}>
              <button className={`thread ${threadId === t.id ? "is-on" : ""}`} onClick={() => setThreadId(t.id)}>
                <span className="thread-title">{t.title}</span>
                <span className="thread-meta">
                  {Math.floor(t.message_count / 2)} answer{t.message_count === 2 ? "" : "s"} ·{" "}
                  {new Date(t.updated_at).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <Conversation
        key={threadId ?? "new"}
        matterId={props.matterId}
        threadId={threadId}
        models={models.data?.allowed ?? []}
        defaultModel={models.data?.default ?? null}
        backend={models.data?.backend ?? null}
        modelsError={models.error}
        onCreated={(id) => {
          setThreadId(id);
          setRev((r) => r + 1);
        }}
        onAnswered={() => setRev((r) => r + 1)}
        onInspect={props.onInspect}
        onOpenDraft={props.onOpenDraft}
      />
    </section>
  );
}

function Conversation(props: {
  matterId: string;
  threadId: string | null;
  models: string[];
  defaultModel: string | null;
  backend: string | null;
  modelsError: string | null;
  onCreated: (id: string) => void;
  onAnswered: () => void;
  onInspect: (s: Selection) => void;
  onOpenDraft: (draftId: string) => void;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [live, setLive] = useState<Live | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const pendingThread = useRef<string | null>(null);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!props.threadId || props.threadId === pendingThread.current) return;
    api
      .thread(props.threadId)
      .then((t) => setMessages(t.messages))
      .catch((e) => setLoadErr(errorText(e)));
  }, [props.threadId]);

  useEffect(() => {
    end.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [messages.length, live?.text]);

  async function send(body: AskBody): Promise<void> {
    let tid = props.threadId;
    setLive({ question: body.content, mode: body.mode, text: "", sources: [], notice: null, error: null });
    try {
      if (!tid) {
        tid = (await api.createThread(props.matterId)).id;
        pendingThread.current = tid;
      }
      let final: ChatMessage | null = null;
      let question: ChatMessage | null = null;
      await api.ask(tid, body, (ev) => {
        if (ev.type === "meta") {
          question = {
            ...emptyMessage(tid!, "user", body.mode),
            id: ev.data.question_id,
            content: body.content,
          };
          setLive((l) => l && { ...l, sources: ev.data.sources });
        } else if (ev.type === "delta") {
          setLive((l) => l && { ...l, text: l.text + ev.data.text, notice: null });
        } else if (ev.type === "notice") {
          setLive((l) => l && { ...l, notice: ev.data.text });
        } else if (ev.type === "done") {
          final = ev.data;
        } else if (ev.type === "error") {
          final = ev.data.message ?? null;
          setLive((l) => l && { ...l, error: ev.data.detail });
        }
      });
      const q = question as ChatMessage | null;
      const f = final as ChatMessage | null;
      if (q && f) {
        setMessages((m) => [...m, q, f]); // a failed answer shows its own error
        setLive(null);
      }
      if (!props.threadId) props.onCreated(tid);
      else props.onAnswered();
    } catch (e) {
      setLive((l) => l && { ...l, error: errorText(e) });
    }
  }

  const busy = !!live && !live.error;

  return (
    <div className="convo">
      <div className="convo-log">
        {loadErr && <ErrorNote>{loadErr}</ErrorNote>}
        {!props.threadId && messages.length === 0 && !live && (
          <Empty title="Ask about this matter">
            Answers use only this matter's indexed documents. Every statement cites its source, and each sentence is
            checked against the passage it cites.
          </Empty>
        )}
        {messages.map((m) =>
          m.role === "user" ? (
            <Question key={m.id} text={m.content} mode={m.mode} kind={m.options?.draft_kind ?? null} />
          ) : (
            <Answer key={m.id} m={m} matterId={props.matterId} onInspect={props.onInspect} onOpenDraft={props.onOpenDraft} />
          ),
        )}
        {live && (live.error || busy) && (
          <>
            <Question text={live.question} mode={live.mode} kind={null} />
            <div className="answer is-live" aria-live="polite">
              {live.notice && <p className="note">{live.notice}</p>}
              {live.text ? (
                <p className="ans-block">
                  <LiveText text={live.text} sources={live.sources} />
                </p>
              ) : (
                !live.error && <p className="muted">Finding passages and writing…</p>
              )}
              {live.error && <ErrorNote>{live.error}</ErrorNote>}
            </div>
          </>
        )}
        <div ref={end} />
      </div>
      <Composer
        matterId={props.matterId}
        busy={busy}
        models={props.models}
        defaultModel={props.defaultModel}
        backend={props.backend}
        modelsError={props.modelsError}
        onSend={send}
      />
    </div>
  );
}

function emptyMessage(threadId: string, role: "user" | "assistant", mode: Mode): ChatMessage {
  return {
    id: "",
    thread_id: threadId,
    role,
    content: "",
    mode,
    options: {},
    sources: [],
    sentences: [],
    verification: {},
    status: "complete",
    error: null,
    model: null,
    input_tokens: null,
    output_tokens: null,
    latency_ms: null,
    cost_usd: null,
    created_at: new Date().toISOString(),
  };
}

function Question({ text, mode, kind }: { text: string; mode: Mode; kind: DraftKind | null }) {
  return (
    <div className="question">
      <span className="tag">{mode === "draft" && kind ? `Draft · ${DRAFTS.find((d) => d.id === kind)?.name}` : label(mode)}</span>
      <p>{text}</p>
    </div>
  );
}

/** While streaming: show the text with [S1] markers already turned into numbered chips. */
function LiveText({ text, sources }: { text: string; sources: AnswerSource[] }) {
  const parts = text.split(/(\[\s*S\d+(?:\s*[,;]\s*S\d+)*\s*\])/g);
  return (
    <>
      {parts.map((p, i) => {
        const keys = p.match(/S\d+/g);
        if (i % 2 === 1 && keys) {
          return keys.map((k) => {
            const n = sources.findIndex((s) => s.key === k) + 1;
            return (
              <sup key={`${i}-${k}`} className="cite cite-live">
                {n || k}
              </sup>
            );
          });
        }
        return <span key={i}>{p}</span>;
      })}
    </>
  );
}

const TERM = /[\p{L}\p{N}][\p{L}\p{N}'.,-]{3,}/gu;

function Answer({
  m,
  matterId,
  onInspect,
  onOpenDraft,
}: {
  m: ChatMessage;
  matterId: string;
  onInspect: (s: Selection) => void;
  onOpenDraft: (draftId: string) => void;
}) {
  const [showSources, setShowSources] = useState(false);
  const [exportErr, setExportErr] = useState<string | null>(null);
  const [making, setMaking] = useState(false);
  const editAsDraft = async () => {
    setMaking(true);
    try {
      onOpenDraft((await api.createDraft(matterId, { from_message_id: m.id })).id);
    } catch (e) {
      setExportErr(errorText(e));
    } finally {
      setMaking(false);
    }
  };
  const number = new Map(m.sources.map((s, i) => [s.key, i + 1]));
  const byKey = new Map(m.sources.map((s) => [s.key, s]));
  const blocks: AnswerSentence[][] = [];
  m.sentences.forEach((s) => {
    (blocks[s.block] ??= []).push(s);
  });
  const v = m.verification;
  const flagged = (v.unsupported ?? 0) + (v.partial ?? 0);

  const open = (key: string, sentence: string) => {
    const src = byKey.get(key);
    if (src)
      onInspect({
        kind: "chunk",
        docId: src.document_id,
        chunkId: src.chunk_id,
        terms: [...new Set(sentence.match(TERM) ?? [])].slice(0, 12),
      });
  };

  return (
    <div className={`answer ${m.status === "failed" ? "is-failed" : ""}`}>
      {m.sentences.length > 0 ? (
        blocks.filter(Boolean).map((b, i) => (
          <p key={i} className={`ans-block ${b[0]?.prefix ? "is-item" : ""}`}>
            {b[0]?.prefix && <span className="ans-prefix">{b[0].prefix.trim()}</span>}
            <span>
              {b.map((s, j) => (
                <span key={j}>
                  <span className={`sent s-${s.label}`} title={s.reason || undefined}>
                    {s.text}
                  </span>
                  {s.cites.map((k) => (
                    <button
                      key={k}
                      className="cite"
                      onClick={() => open(k, s.text)}
                      title={byKey.get(k) ? `${byKey.get(k)!.document_title}, p. ${byKey.get(k)!.page_from}` : k}
                    >
                      {number.get(k) ?? k}
                    </button>
                  ))}{" "}
                </span>
              ))}
            </span>
          </p>
        ))
      ) : (
        <p className="ans-block">{m.content || <span className="muted">No text was produced.</span>}</p>
      )}
      {m.error && <ErrorNote>{m.error}</ErrorNote>}

      {m.status === "complete" && (
        <div className="ans-foot">
          {m.model && (
            <span className="verdict" aria-label="Verification">
              <span className="v v-supported">{v.supported ?? 0} supported</span>
              {(v.partial ?? 0) > 0 && <span className="v v-partial">{v.partial} partly</span>}
              {(v.unsupported ?? 0) > 0 && <span className="v v-unsupported">{v.unsupported} not supported</span>}
            </span>
          )}
          {m.sources.length > 0 && (
            <button className="btn btn-quiet btn-small" onClick={() => setShowSources(!showSources)} aria-expanded={showSources}>
              {showSources ? "Hide" : "Show"} {m.sources.length} source{m.sources.length === 1 ? "" : "s"}
            </button>
          )}
          <span className="toolbar-gap" />
          {m.model && (
            <span className="ans-meta">
              {m.model} · {(m.input_tokens ?? 0).toLocaleString("en-GB")} in / {(m.output_tokens ?? 0).toLocaleString("en-GB")}{" "}
              out · {((m.latency_ms ?? 0) / 1000).toFixed(1)} s · ${(m.cost_usd ?? 0).toFixed(4)}
            </span>
          )}
          {m.id && (
            <button
              className="btn btn-small"
              onClick={() => api.exportAnswer(m.thread_id, m.id).catch((e) => setExportErr(errorText(e)))}
            >
              Export to Word
            </button>
          )}
          {m.id && m.model && (
            <button className="btn btn-small" onClick={editAsDraft} disabled={making} title="Open this answer as a draft everyone on the matter can edit, version and approve">
              {making ? "Opening…" : "Edit as draft"}
            </button>
          )}
        </div>
      )}
      {flagged > 0 && (
        <p className="ans-legend">
          Underlined sentences could not be fully matched to their cited passage. Hover for the reason; open the
          citation to check it.
        </p>
      )}
      {v.note && <p className="ans-legend">{v.note}</p>}
      {exportErr && <ErrorNote>{exportErr}</ErrorNote>}
      {showSources && (
        <ol className="ans-sources">
          {m.sources.map((s) => (
            <li key={s.key}>
              <button className="src" onClick={() => open(s.key, s.excerpt)}>
                <span className="src-title">
                  {s.document_title}, {s.page_from === s.page_to ? `p. ${s.page_from}` : `pp. ${s.page_from}–${s.page_to}`}
                  {s.section_path ? ` · ${s.section_path}` : ""}
                </span>
                <span className="src-excerpt">{s.excerpt}</span>
              </button>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function Composer(props: {
  matterId: string;
  busy: boolean;
  models: string[];
  defaultModel: string | null;
  backend: string | null;
  modelsError: string | null;
  onSend: (b: AskBody) => Promise<void>;
}) {
  const [text, setText] = useState("");
  const [mode, setMode] = useState<Mode>("ask");
  const [kind, setKind] = useState<DraftKind>("notice");
  const [model, setModel] = useState("auto");
  const [picking, setPicking] = useState(false);
  const [chosen, setChosen] = useState<string[]>([]);
  const docs = useLoad(() => api.documents(props.matterId), [props.matterId]);
  const usable = (docs.data ?? []).filter((d) => d.status === "indexed" || d.status === "partial");
  const canSend = !props.busy && (mode === "summarise" ? true : text.trim().length >= 2);

  async function submit(e?: FormEvent) {
    e?.preventDefault();
    if (!canSend) return;
    const content = text.trim() || "Summarise the key points";
    setText("");
    await props.onSend({
      content,
      mode,
      draft_kind: mode === "draft" ? kind : null,
      document_ids: chosen.length ? chosen : null,
      model,
    });
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void submit();
    }
  }

  return (
    <form className="composer" onSubmit={submit}>
      <div className="composer-row">
        <div className="seg" role="radiogroup" aria-label="Mode">
          {MODES.map((m) => (
            <button
              key={m.id}
              type="button"
              role="radio"
              aria-checked={mode === m.id}
              className={mode === m.id ? "is-on" : ""}
              title={m.hint}
              onClick={() => setMode(m.id)}
            >
              {m.name}
            </button>
          ))}
        </div>
        {mode === "draft" && (
          <select value={kind} onChange={(e) => setKind(e.target.value as DraftKind)} aria-label="Draft type">
            {DRAFTS.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        )}
        <button type="button" className={`pick ${chosen.length ? "is-on" : ""}`} onClick={() => setPicking(!picking)} aria-expanded={picking}>
          {chosen.length ? `${chosen.length} document${chosen.length === 1 ? "" : "s"}` : "Whole matter"}
        </button>
        <span className="toolbar-gap" />
        <select value={model} onChange={(e) => setModel(e.target.value)} aria-label="Model" title={props.modelsError ?? "Model"}>
          <option value="auto">Auto{props.defaultModel ? ` (${props.defaultModel})` : ""}</option>
          {props.models.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </div>
      {props.backend === "fake" && (
        <p className="note">
          The API is using the offline test model, which only quotes passages. Set DATAVIV_LLM_BACKEND=groq in
          backend\.env and restart to use Groq.
        </p>
      )}
      {props.modelsError && <ErrorNote>{props.modelsError}</ErrorNote>}
      {picking && (
        <div className="doc-pick card">
          {usable.length === 0 && <p className="muted">No indexed documents yet.</p>}
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
          {chosen.length > 0 && (
            <button type="button" className="btn btn-quiet btn-small" onClick={() => setChosen([])}>
              Use the whole matter
            </button>
          )}
        </div>
      )}
      <div className="composer-input">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          placeholder={PLACEHOLDER[mode]}
          rows={2}
          aria-label="Your question or instructions"
        />
        <button className="btn btn-primary" disabled={!canSend}>
          {props.busy ? "Writing…" : mode === "ask" ? "Ask" : mode === "summarise" ? "Summarise" : "Draft"}
        </button>
      </div>
    </form>
  );
}
