/**
 * Live co-editing of a draft (Step 2c): CodeMirror 6 bound to a shared Yjs text, synced over
 * /v1/drafts/{id}/live. Everyone's typing merges as they go; cursors and selections of the others are shown.
 *
 * The server is the authority: a viewer, or anyone while the draft waits for approval, is read-only.
 * If an edit is refused anyway (race with a lock), the server closes the socket (4403 / 4423) and this
 * component throws its local copy away and reconnects, so nobody keeps text the others cannot see.
 */
import { defaultKeymap } from "@codemirror/commands";
import { Compartment, EditorState, RangeSetBuilder, StateEffect, StateField } from "@codemirror/state";
import { Decoration, EditorView, keymap, placeholder, type DecorationSet } from "@codemirror/view";
import * as decoding from "lib0/decoding";
import { useEffect, useRef, useState, type MutableRefObject } from "react";
import { yCollab, yUndoManagerKeymap } from "y-codemirror.next";
import { WebsocketProvider } from "y-websocket";
import * as Y from "yjs";
import { currentToken } from "../api";

export type LiveEvent =
  | { type: "hello"; can_edit: boolean; locked: boolean }
  | { type: "status"; status: string; locked: boolean }
  | { type: "access"; can_edit: boolean }
  | { type: "changed"; what: string; by: string; [k: string]: unknown };

export type Peer = { clientId: number; name: string; colour: string };
export type LiveSelection = { text: string; start: number };
export type LiveHandle = { selection: () => LiveSelection | null; value: () => string };
type Conn = "connecting" | "connected" | "offline" | "refused";

const MSG_APP = 100;
const REFUSED: Record<number, string> = {
  4401: "Your session has expired. Switch user again to reconnect.",
  4404: "You no longer have access to this matter.",
  4503: "Live editing is not available on this server.",
};

function colourFor(name: string): string {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return `hsl(${h} 70% 42%)`;
}

// ---- highlighting the passages that pending AI suggestions would change
const setQuotes = StateEffect.define<string[]>();
const quoteMark = Decoration.mark({ class: "cm-suggested" });

function marks(doc: string, quotes: string[]): DecorationSet {
  const ranges: [number, number][] = [];
  for (const q of quotes) {
    if (!q) continue;
    let i = doc.indexOf(q);
    while (i !== -1) {
      ranges.push([i, i + q.length]);
      i = doc.indexOf(q, i + q.length);
    }
  }
  ranges.sort((a, b) => a[0] - b[0]);
  const b = new RangeSetBuilder<Decoration>();
  let last = -1;
  for (const [from, to] of ranges) {
    if (from >= last) {
      b.add(from, to, quoteMark);
      last = to;
    }
  }
  return b.finish();
}

const quotesField = StateField.define<{ quotes: string[]; deco: DecorationSet }>({
  create: () => ({ quotes: [], deco: Decoration.none }),
  update(value, tr) {
    let quotes = value.quotes;
    for (const e of tr.effects) if (e.is(setQuotes)) quotes = e.value;
    if (quotes === value.quotes && !tr.docChanged) return value;
    return { quotes, deco: marks(tr.state.doc.toString(), quotes) };
  },
  provide: (f) => EditorView.decorations.from(f, (v) => v.deco),
});

export function LiveEditor(props: {
  draftId: string;
  userName: string;
  canEdit: boolean;
  locked: boolean;
  highlights: string[];
  handleRef: MutableRefObject<LiveHandle | null>;
  onEvent: (e: LiveEvent) => void;
  onLocalEdit: () => void;
  onSelection: (s: LiveSelection | null) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const editable = useRef(new Compartment());
  const [generation, setGeneration] = useState(0);
  const [conn, setConn] = useState<Conn>("connecting");
  const [refused, setRefused] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [peers, setPeers] = useState<Peer[]>([]);
  const cb = useRef(props);
  cb.current = props;

  useEffect(() => {
    if (!host.current) return;
    const ydoc = new Y.Doc();
    const ytext = ydoc.getText("content");
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    const provider = new WebsocketProvider(`${scheme}://${window.location.host}/v1/drafts`, `${props.draftId}/live`, ydoc, {
      protocols: ["dataviv", currentToken() ?? ""], // the token never goes into the URL
    });
    provider.messageHandlers[MSG_APP] = (_enc, dec) => {
      const event = JSON.parse(decoding.readVarString(dec)) as LiveEvent;
      cb.current.onEvent(event);
    };
    provider.awareness.setLocalStateField("user", {
      name: props.userName,
      color: colourFor(props.userName),
      colorLight: colourFor(props.userName).replace("42%)", "42% / 0.18)"),
    });
    const onAwareness = () => {
      const list: Peer[] = [];
      provider.awareness.getStates().forEach((s, id) => {
        const u = (s as { user?: { name?: string; color?: string } }).user;
        if (id !== ydoc.clientID && u?.name) list.push({ clientId: id, name: u.name, colour: u.color ?? "#888" });
      });
      setPeers(list);
    };
    provider.awareness.on("change", onAwareness);
    provider.on("status", ({ status }) => setConn(status === "connected" ? "connected" : status === "connecting" ? "connecting" : "offline"));
    provider.on("connection-close", (event) => {
      const code = event?.code ?? 0;
      if (code === 4403 || code === 4423) {
        // An edit was refused (read-only or locked for approval): start again from the server's copy.
        setNotice(
          code === 4423
            ? "The draft was sent for approval while you were typing; your last change was not applied."
            : "You can read this draft but not edit it.",
        );
        setTimeout(() => setGeneration((g) => g + 1), 50);
      } else if (REFUSED[code]) {
        setRefused(REFUSED[code]);
        setConn("refused");
      }
    });
    ytext.observe((e) => {
      if (e.transaction.local) cb.current.onLocalEdit();
    });

    const undoManager = new Y.UndoManager(ytext);
    const v = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: "",
        extensions: [
          keymap.of([...yUndoManagerKeymap, ...defaultKeymap]),
          EditorView.lineWrapping,
          placeholder("Start writing. Everyone on this matter sees your changes as you type."),
          editable.current.of(EditorView.editable.of(false)),
          yCollab(ytext, provider.awareness, { undoManager }),
          quotesField,
          EditorView.updateListener.of((u) => {
            if (!u.selectionSet && !u.docChanged) return;
            const sel = u.state.selection.main;
            cb.current.onSelection(sel.empty ? null : { text: u.state.sliceDoc(sel.from, sel.to), start: sel.from });
          }),
        ],
      }),
    });
    view.current = v;
    props.handleRef.current = {
      value: () => v.state.doc.toString(),
      selection: () => {
        const sel = v.state.selection.main;
        return sel.empty ? null : { text: v.state.sliceDoc(sel.from, sel.to), start: sel.from };
      },
    };
    return () => {
      props.handleRef.current = null;
      v.destroy();
      view.current = null;
      provider.awareness.off("change", onAwareness);
      provider.destroy();
      ydoc.destroy();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.draftId, generation]);

  useEffect(() => {
    view.current?.dispatch({
      effects: editable.current.reconfigure(EditorView.editable.of(props.canEdit && !props.locked && conn === "connected")),
    });
  }, [props.canEdit, props.locked, conn, generation]);

  useEffect(() => {
    view.current?.dispatch({ effects: setQuotes.of(props.highlights) });
  }, [props.highlights, generation]);

  return (
    <div className="live-editor">
      <div className="live-bar">
        <span className={`live-dot live-${conn}`} aria-hidden="true" />
        <span className="muted">
          {conn === "connected"
            ? props.locked
              ? "Live · locked while waiting for approval"
              : props.canEdit
                ? "Live · changes are shared as you type"
                : "Live · read only"
            : conn === "connecting"
              ? "Connecting…"
              : conn === "refused"
                ? refused
                : "Offline · reconnecting (your typing is kept and sent when back)"}
        </span>
        <span className="toolbar-gap" />
        {peers.map((p) => (
          <span key={p.clientId} className="peer" style={{ borderColor: p.colour, color: p.colour }} title={`${p.name} is here`}>
            {p.name.split(" ")[0]}
          </span>
        ))}
      </div>
      {notice && (
        <p className="note note-warn">
          {notice}{" "}
          <button className="link" onClick={() => setNotice(null)}>
            OK
          </button>
        </p>
      )}
      <div ref={host} className="page live-page" aria-label="Draft text (live)" />
    </div>
  );
}
