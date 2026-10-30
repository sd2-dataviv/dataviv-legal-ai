import { useState, type FormEvent } from "react";
import { api, errorText, label, when, type Identity, type MatterRole } from "../api";
import { useLoad } from "../hooks";
import { Empty, ErrorNote } from "./ui";

export function PeopleTab({ matterId, who }: { matterId: string; who: Identity }) {
  const people = useLoad(() => api.people(matterId), [matterId]);
  const users = useLoad(() => api.users(), []);
  const memberIds = new Set(people.data?.members.map((m) => m.user_id));
  const walledIds = new Set(people.data?.walls.map((w) => w.user_id));
  const candidates = (users.data ?? []).filter((u) => !memberIds.has(u.id));

  return (
    <section className="people">
      <p className="note">
        The console shows every action to every role. Whether it is allowed is the API's call, so a refusal here is a
        result worth checking, not a bug in the console.
      </p>
      {people.error && <ErrorNote>{people.error}</ErrorNote>}

      <h3>Members</h3>
      {people.data && (
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Firm role</th>
              <th>Matter role</th>
            </tr>
          </thead>
          <tbody>
            {people.data.members.map((m) => (
              <tr key={m.user_id} className="is-static">
                <td className="cell-title">
                  {m.name}
                  {m.user_id === who.user_id && <span className="tag tag-you">you</span>}
                  <small className="muted">{m.email}</small>
                </td>
                <td>{label(m.firm_role)}</td>
                <td>{label(m.matter_role)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <AddMember
        matterId={matterId}
        candidates={candidates.map((u) => ({ ...u, walled: walledIds.has(u.id) }))}
        onDone={people.reload}
      />

      <h3>Ethical walls</h3>
      {people.data && people.data.walls.length === 0 && (
        <Empty title="No walls on this matter">A firm admin can screen a person off the matter below.</Empty>
      )}
      {people.data && people.data.walls.length > 0 && (
        <ul className="walls">
          {people.data.walls.map((w) => (
            <li key={w.user_id} className="wall">
              <strong>{w.name}</strong> is screened off since {when(w.created_at)}
              <p className="muted">{w.reason}</p>
            </li>
          ))}
        </ul>
      )}
      <RaiseWall matterId={matterId} people={users.data ?? []} walled={walledIds} onDone={people.reload} />
    </section>
  );
}

function AddMember(props: {
  matterId: string;
  candidates: { id: string; name: string; role: string; walled: boolean }[];
  onDone: () => void;
}) {
  const [userId, setUserId] = useState("");
  const [role, setRole] = useState<MatterRole>("member");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setMsg(null);
    try {
      await api.addMember(props.matterId, userId, role);
      setMsg({ ok: true, text: "Member added." });
      setUserId("");
      props.onDone();
    } catch (e2) {
      setMsg({ ok: false, text: errorText(e2) });
    }
  }

  return (
    <form className="form form-row card" onSubmit={submit}>
      <label className="field">
        <span>Add a member</span>
        <select value={userId} onChange={(e) => setUserId(e.target.value)} required>
          <option value="">Choose someone in your firm</option>
          {props.candidates.map((u) => (
            <option key={u.id} value={u.id}>
              {u.name} · {u.role}
              {u.walled ? " (walled)" : ""}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>Matter role</span>
        <select value={role} onChange={(e) => setRole(e.target.value as MatterRole)}>
          {(["lead", "partner", "member", "viewer"] as const).map((r) => (
            <option key={r} value={r}>
              {label(r)}
            </option>
          ))}
        </select>
      </label>
      <button className="btn btn-primary">Add member</button>
      {msg && <p className={`note ${msg.ok ? "note-ok" : "note-error"} form-msg`}>{msg.text}</p>}
    </form>
  );
}

function RaiseWall(props: {
  matterId: string;
  people: { id: string; name: string; role: string }[];
  walled: Set<string>;
  onDone: () => void;
}) {
  const [userId, setUserId] = useState("");
  const [reason, setReason] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setMsg(null);
    try {
      await api.raiseWall(props.matterId, userId, reason);
      setMsg({ ok: true, text: "Wall raised. That person can no longer open this matter." });
      setUserId("");
      setReason("");
      props.onDone();
    } catch (e2) {
      setMsg({ ok: false, text: errorText(e2) });
    }
  }

  return (
    <form className="form form-row card" onSubmit={submit}>
      <label className="field">
        <span>Screen off</span>
        <select value={userId} onChange={(e) => setUserId(e.target.value)} required>
          <option value="">Choose someone</option>
          {props.people
            .filter((u) => !props.walled.has(u.id))
            .map((u) => (
              <option key={u.id} value={u.id}>
                {u.name} · {u.role}
              </option>
            ))}
        </select>
      </label>
      <label className="field field-grow">
        <span>Reason</span>
        <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Acted for the opposite party in 2023" />
      </label>
      <button className="btn btn-danger">Raise wall</button>
      {msg && <p className={`note ${msg.ok ? "note-ok" : "note-error"} form-msg`}>{msg.text}</p>}
    </form>
  );
}
