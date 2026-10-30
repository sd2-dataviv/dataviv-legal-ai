import { useState } from "react";
import {
  api,
  errorText,
  when,
  type DraftDetail,
  type ESignStatus,
  type FirmUser,
  type Identity,
  type SignatureRequest,
  type SignerInput,
} from "../api";
import { useLoad } from "../hooks";
import type { Selection } from "./Inspector";
import { ErrorNote } from "./ui";

const SIGNATURE: Record<SignatureRequest["status"], string> = {
  sent: "Out for signature",
  delivered: "Being signed",
  completed: "Signed by all",
  declined: "Declined",
  voided: "Voided",
  failed: "Failed",
};
const SIGNER: Record<string, string> = {
  created: "waiting for earlier signers",
  sent: "e-mailed",
  delivered: "opened the e-mail",
  signed: "signed",
  declined: "declined",
  voided: "voided",
};
const MANAGERS = ["lead", "partner"];

/** Send the approved version of a draft for e-signature and follow it until everyone has signed. */
export function SignaturePanel(props: {
  d: DraftDetail;
  who: Identity;
  onInspect: (s: Selection) => void;
  onDone: () => void;
}) {
  const { d } = props;
  const status = useLoad(() => api.esignStatus(), []);
  const manager = MANAGERS.includes(d.my_matter_role);
  const open = d.signatures.find((s) => s.status === "sent" || s.status === "delivered");
  const st = status.data;
  const [again, setAgain] = useState(false);

  return (
    <div className="sig-panel">
      {status.error && <ErrorNote>{status.error}</ErrorNote>}
      {st && <ProviderNote st={st} />}
      {!d.approved_version_no && (
        <p className="muted">Only an approved version can be signed. Ask the matter lead or a partner to approve the draft first.</p>
      )}
      {d.signatures.length > 0 && (
        <ol className="sig-list">
          {d.signatures.map((s) => (
            <SignatureCard
              key={s.id}
              s={s}
              d={d}
              testMode={st?.backend === "fake"}
              manager={manager}
              onInspect={props.onInspect}
              onDone={props.onDone}
            />
          ))}
        </ol>
      )}
      {d.approved_version_no && !open && st?.ready && (manager ? (
        d.status === "in_review" ? (
          <p className="muted">A newer version is waiting for approval. Send for signature after the decision.</p>
        ) : d.signatures.length > 0 && !again ? (
          <button className="link" onClick={() => setAgain(true)}>
            Send v{d.approved_version_no} for signature again…
          </button>
        ) : (
          <SendForm d={d} who={props.who} onDone={() => { setAgain(false); props.onDone(); }} />
        )
      ) : (
        d.signatures.length === 0 && (
          <p className="muted">The matter lead or a partner can send v{d.approved_version_no} for signature.</p>
        )
      ))}
    </div>
  );
}

function ProviderNote({ st }: { st: ESignStatus }) {
  if (!st.ready) {
    return (
      <div className="note note-warn">
        <p>E-signature is not available: {st.detail}</p>
        {st.consent_url && (
          <p>
            <a href={st.consent_url} target="_blank" rel="noreferrer">
              Open the DocuSign consent page
            </a>{" "}
            (once, as the DocuSign user the app signs in as).
          </p>
        )}
      </div>
    );
  }
  if (st.backend === "fake") {
    return (
      <p className="note">
        <strong>Test mode.</strong> No e-mails are sent and nothing is legally signed. Use the buttons on an envelope to act as
        its signers.
      </p>
    );
  }
  return (
    <p className="muted small">
      DocuSign {st.environment === "sandbox" ? "developer sandbox" : "production"} · {st.account_name}
      {st.callbacks ? " · live updates on" : " · status checked every minute"}
    </p>
  );
}

function SendForm(props: { d: DraftDetail; who: Identity; onDone: () => void }) {
  const { d } = props;
  const users = useLoad(() => api.users(), []);
  const n = d.approved_version_no!;
  const [rows, setRows] = useState<SignerInput[]>(() => [{ name: props.who.name, email: props.who.email, routing_order: 1 }]);
  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const valid = rows.length > 0 && rows.every((r) => r.name.trim() && /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(r.email.trim()));
  const set = (i: number, patch: Partial<SignerInput>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const nextOrder = () => Math.max(0, ...rows.map((r) => r.routing_order)) + 1;

  function addColleague(u: FirmUser) {
    if (rows.some((r) => r.email.toLowerCase() === u.email.toLowerCase())) return;
    setRows([...rows, { name: u.name, email: u.email, routing_order: nextOrder() }]);
  }

  async function send() {
    setBusy(true);
    setErr(null);
    try {
      await api.sendForSignature(d.id, {
        signers: rows.map((r) => ({ ...r, name: r.name.trim(), email: r.email.trim() })),
        subject: subject.trim() || null,
        message: message.trim() || null,
        confirm_not_excluded: confirmed,
      });
      props.onDone();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="sig-form">
      <strong>Send approved v{n} for signature</strong>
      {d.current_version_no !== n && (
        <p className="note note-warn">
          The draft has changed since v{n} was approved. Signers receive the approved v{n}, not v{d.current_version_no}.
        </p>
      )}
      <div className="sig-rows">
        <span className="muted small">Order</span>
        <span className="muted small">Name</span>
        <span className="muted small">E-mail</span>
        <span />
        {rows.map((r, i) => (
          <SignerRow key={i} r={r} onChange={(p) => set(i, p)} onRemove={rows.length > 1 ? () => setRows(rows.filter((_, j) => j !== i)) : null} />
        ))}
      </div>
      <div className="row">
        <button className="link" onClick={() => setRows([...rows, { name: "", email: "", routing_order: nextOrder() }])}>
          + Add a signer
        </button>
        {users.data && (
          <select
            value=""
            onChange={(e) => {
              const u = users.data!.find((x) => x.id === e.target.value);
              if (u) addColleague(u);
            }}
            aria-label="Add a colleague as signer"
          >
            <option value="">+ Add a colleague…</option>
            {users.data.map((u) => (
              <option key={u.id} value={u.id}>
                {u.name} ({u.role})
              </option>
            ))}
          </select>
        )}
      </div>
      <p className="muted small">Signers with the same order sign in parallel; a higher number waits for the lower ones.</p>
      <input value={subject} onChange={(e) => setSubject(e.target.value)} placeholder={`Please sign: ${d.title} (version ${n})`} aria-label="E-mail subject" />
      <textarea value={message} onChange={(e) => setMessage(e.target.value)} rows={2} placeholder="Message to the signers (optional)" aria-label="Message to signers" />
      <label className="check">
        <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
        <span>
          This is not a power of attorney, trust deed, will, negotiable instrument (other than a cheque), or a contract to sell or
          transfer immovable property. Indian law (IT Act 2000) does not allow those to be signed electronically.
        </span>
      </label>
      {err && <ErrorNote>{err}</ErrorNote>}
      <button className="btn btn-primary" onClick={send} disabled={!valid || !confirmed || busy}>
        {busy ? "Sending…" : `Send for signature (${rows.length} signer${rows.length === 1 ? "" : "s"})`}
      </button>
    </div>
  );
}

function SignerRow(props: { r: SignerInput; onChange: (p: Partial<SignerInput>) => void; onRemove: (() => void) | null }) {
  const { r } = props;
  return (
    <>
      <input
        type="number"
        min={1}
        max={20}
        value={r.routing_order}
        onChange={(e) => props.onChange({ routing_order: Math.max(1, Math.min(20, Number(e.target.value) || 1)) })}
        aria-label="Signing order"
      />
      <input value={r.name} onChange={(e) => props.onChange({ name: e.target.value })} placeholder="Full name" aria-label="Signer name" />
      <input value={r.email} onChange={(e) => props.onChange({ email: e.target.value })} placeholder="name@example.com" aria-label="Signer e-mail" />
      {props.onRemove ? (
        <button className="link" onClick={props.onRemove} aria-label={`Remove ${r.name || "signer"}`}>
          ✕
        </button>
      ) : (
        <span />
      )}
    </>
  );
}

function SignatureCard(props: {
  s: SignatureRequest;
  d: DraftDetail;
  testMode: boolean;
  manager: boolean;
  onInspect: (s: Selection) => void;
  onDone: () => void;
}) {
  const { s } = props;
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [voiding, setVoiding] = useState(false);
  const [reason, setReason] = useState("");
  const isOpen = s.status === "sent" || s.status === "delivered";
  const canAct = props.manager || props.d.my_matter_role === "member";

  async function act(name: string, fn: () => Promise<unknown>) {
    setBusy(name);
    setErr(null);
    try {
      await fn();
      props.onDone();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <li className={`sig-card sig-${s.status}`}>
      <div className="ver-head">
        <strong>v{s.version_no}</strong>
        <span className={`chip chip-sig-${s.status}`}>{SIGNATURE[s.status]}</span>
        <span className="muted small">{s.provider === "docusign" ? "DocuSign" : "Test mode"}</span>
      </div>
      <p className="ver-note">
        {s.sent_by.name} sent it {when(s.sent_at)} · “{s.subject}”
      </p>
      <ol className="signer-list">
        {s.signers.map((x) => (
          <li key={x.email} className={`signer signer-${x.status}`}>
            <span className="signer-order">{x.routing_order}</span>
            <span className="signer-who">
              <strong>{x.name}</strong> <span className="muted small">{x.email}</span>
              <br />
              <span className={`signer-status signer-status-${x.status}`}>
                {SIGNER[x.status] ?? x.status}
                {x.signed_at ? ` · ${when(x.signed_at)}` : ""}
              </span>
              {x.declined_reason && <span className="small"> · “{x.declined_reason}”</span>}
            </span>
          </li>
        ))}
      </ol>
      {s.voided_reason && <p className="ver-note">Voided: {s.voided_reason}</p>}
      {s.last_error && <p className="sug-warning">{s.last_error}</p>}
      {s.status === "completed" && (
        <div className="row">
          {s.signed_document_id && (
            <button className="btn btn-small btn-primary" onClick={() => props.onInspect({ kind: "doc", docId: s.signed_document_id! })}>
              Open signed copy
            </button>
          )}
          {s.certificate_document_id && (
            <button className="btn btn-small" onClick={() => props.onInspect({ kind: "doc", docId: s.certificate_document_id! })}>
              Certificate of completion
            </button>
          )}
        </div>
      )}
      {isOpen && (
        <div className="row">
          <button className="btn btn-small" disabled={busy !== null} onClick={() => act("refresh", () => api.refreshSignature(s.id))}>
            {busy === "refresh" ? "Checking…" : "Check status"}
          </button>
          {props.manager && !voiding && (
            <button className="btn btn-small btn-quiet btn-danger-text" onClick={() => setVoiding(true)}>
              Void…
            </button>
          )}
          {s.last_checked_at && <span className="muted small">checked {when(s.last_checked_at)}</span>}
        </div>
      )}
      {isOpen && voiding && (
        <div className="row">
          <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason (signers see it)" aria-label="Reason for voiding" />
          <button
            className="btn btn-small btn-danger-text"
            disabled={!reason.trim() || busy !== null}
            onClick={() => act("void", () => api.voidSignature(s.id, reason.trim()))}
          >
            Void envelope
          </button>
          <button className="link" onClick={() => setVoiding(false)}>
            Keep it
          </button>
        </div>
      )}
      {isOpen && props.testMode && canAct && (
        <div className="sig-sim">
          <span className="muted small">Act as the next signer (test mode):</span>
          <div className="row">
            <button className="btn btn-small" disabled={busy !== null} onClick={() => act("deliver", () => api.simulateSigning(s.id, "deliver"))}>
              Opens e-mail
            </button>
            <button className="btn btn-small" disabled={busy !== null} onClick={() => act("sign", () => api.simulateSigning(s.id, "sign_next"))}>
              Signs
            </button>
            <button
              className="btn btn-small btn-quiet btn-danger-text"
              disabled={busy !== null}
              onClick={() => act("decline", () => api.simulateSigning(s.id, "decline", "Declined in test mode"))}
            >
              Declines
            </button>
          </div>
        </div>
      )}
      {err && <ErrorNote>{err}</ErrorNote>}
    </li>
  );
}
