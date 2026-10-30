import { useState, type FormEvent } from "react";
import { api, errorText, type MatterCreate } from "../api";
import { useLoad } from "../hooks";
import { Empty, ErrorNote } from "./ui";

const CNR = /^[A-Z]{4}\d{12}$/;

export function MatterRail(props: {
  selected: string | null;
  onSelect: (id: string) => void;
  rev: number;
  onCreated: (id: string) => void;
}) {
  const matters = useLoad(() => api.matters(), [props.rev]);
  const [creating, setCreating] = useState(false);

  return (
    <aside className="rail" aria-label="Matters">
      <div className="pane-head">
        <h2>Matters</h2>
        <button className="btn btn-quiet" onClick={() => setCreating((c) => !c)} aria-expanded={creating}>
          {creating ? "Close" : "New matter"}
        </button>
      </div>
      {creating && (
        <CreateMatter
          onCreated={(id) => {
            setCreating(false);
            props.onCreated(id);
          }}
        />
      )}
      {matters.error && <ErrorNote>{matters.error}</ErrorNote>}
      {matters.data && matters.data.length === 0 && (
        <Empty title="No matters you can open">
          You only see matters you are a member of. Open a new one, or switch to a partner.
        </Empty>
      )}
      <ul className="matter-list">
        {matters.data?.map((m) => (
          <li key={m.id}>
            <button
              className={`matter ${props.selected === m.id ? "is-on" : ""}`}
              onClick={() => props.onSelect(m.id)}
              aria-current={props.selected === m.id}
            >
              <span className="matter-title">{m.title}</span>
              <span className="matter-meta">
                {m.client_name}
                {m.forum ? `, ${m.forum}` : ""}
              </span>
              <span className="matter-tags">
                {m.my_role && <span className="tag">{m.my_role}</span>}
                {m.cnr && <code className="id">{m.cnr}</code>}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </aside>
  );
}

function CreateMatter({ onCreated }: { onCreated: (id: string) => void }) {
  const [f, setF] = useState<MatterCreate>({
    title: "",
    client_name: "",
    matter_type: "litigation",
    forum: null,
    cnr: null,
    practice_area: null,
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const cnrBad = !!f.cnr && !CNR.test(f.cnr);
  const set = (k: keyof MatterCreate) => (v: string) => setF({ ...f, [k]: v === "" ? null : v });

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const m = await api.createMatter({ ...f, title: f.title.trim(), client_name: f.client_name.trim() });
      onCreated(m.id);
    } catch (e2) {
      setErr(errorText(e2));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="form card" onSubmit={submit}>
      <Field label="Title" value={f.title} onChange={(v) => setF({ ...f, title: v })} required />
      <Field label="Client" value={f.client_name} onChange={(v) => setF({ ...f, client_name: v })} required />
      <label className="field">
        <span>Type</span>
        <select value={f.matter_type} onChange={(e) => setF({ ...f, matter_type: e.target.value })}>
          <option value="litigation">Litigation</option>
          <option value="transactional">Transactional</option>
          <option value="advisory">Advisory</option>
          <option value="arbitration">Arbitration</option>
        </select>
      </label>
      <Field label="Forum" value={f.forum ?? ""} onChange={set("forum")} placeholder="Delhi High Court" />
      <Field
        label="CNR"
        value={f.cnr ?? ""}
        onChange={(v) => set("cnr")(v.toUpperCase().replace(/\s/g, ""))}
        placeholder="DLHC010230802020"
        mono
        hint={cnrBad ? "Four letters then 12 digits. The API will reject this; send it anyway to see the 422." : undefined}
      />
      <Field label="Practice area" value={f.practice_area ?? ""} onChange={set("practice_area")} />
      {err && <ErrorNote>{err}</ErrorNote>}
      <button className="btn btn-primary" disabled={busy}>
        {busy ? "Opening…" : "Open matter"}
      </button>
    </form>
  );
}

export function Field(props: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  required?: boolean;
  mono?: boolean;
  hint?: string;
}) {
  return (
    <label className="field">
      <span>{props.label}</span>
      <input
        value={props.value}
        onChange={(e) => props.onChange(e.target.value)}
        placeholder={props.placeholder}
        required={props.required}
        className={props.mono ? "mono" : undefined}
        aria-invalid={props.hint ? true : undefined}
      />
      {props.hint && <small className="field-hint">{props.hint}</small>}
    </label>
  );
}
