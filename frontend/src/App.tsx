import { useEffect, useState } from "react";
import { api, setIdentity, type Identity } from "./api";
import { useInterval, useLoad } from "./hooks";
import { MatterRail } from "./components/MatterRail";
import { Workspace } from "./components/Workspace";
import { Inspector, type Selection } from "./components/Inspector";
import { RequestLog } from "./components/RequestLog";
import { DraftingStudio } from "./components/DraftingStudio";

function stored(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function store(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* storage unavailable: state just won't survive a reload */
  }
}

export function App() {
  const identities = useLoad(() => api.identities(), []);
  const [userId, setUserId] = useState<string | null>(stored("dv.user"));
  const [pasted, setPasted] = useState("");
  const [matterId, setMatterId] = useState<string | null>(stored("dv.matter"));
  const [selection, setSelection] = useState<Selection>(null);
  const [docsRev, setDocsRev] = useState(0);
  const [mattersRev, setMattersRev] = useState(0);
  const [view, setView] = useState<"matters" | "studio">(stored("dv.view") === "studio" ? "studio" : "matters");

  const list = identities.data ?? [];
  const current: Identity | null =
    list.find((i) => i.user_id === userId) ??
    list[0] ??
    (pasted
      ? { firm_id: "", firm_name: "Pasted token", user_id: "pasted", name: "Pasted token", email: "", role: "?", access_token: pasted }
      : null);
  // Set before children render so their first request already carries the right token.
  setIdentity(current?.access_token ?? null, current ? `${current.name} (${current.role})` : "anonymous");

  useEffect(() => store("dv.user", current?.user_id ?? null), [current?.user_id]);
  useEffect(() => store("dv.matter", matterId), [matterId]);
  useEffect(() => store("dv.view", view), [view]);

  const [health, setHealth] = useState<"ok" | "down" | "unknown">("unknown");
  const [env, setEnv] = useState("");
  const ping = () =>
    api
      .health()
      .then((h) => {
        setHealth(h.status === "ok" ? "ok" : "down");
        setEnv(h.env);
      })
      .catch(() => setHealth("down"));
  useEffect(() => {
    void ping();
  }, []);
  useInterval(ping, 15000, true);

  const firms = [...new Set(list.map((i) => i.firm_name))];

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <span className="brand-name">Dataviv AI OS</span>
          <span className="brand-sub">Backend test console</span>
        </div>
        <nav className="view-switch" aria-label="Area">
          <button className={view === "matters" ? "is-on" : ""} onClick={() => setView("matters")}>
            Matters
          </button>
          <button className={view === "studio" ? "is-on" : ""} onClick={() => setView("studio")}>
            Drafting studio
          </button>
        </nav>
        <div className={`health health-${health}`} title="GET /health every 15 s">
          <span className="health-dot" aria-hidden="true" />
          {health === "ok" ? `API up${env ? ` · ${env}` : ""}` : health === "down" ? "API down" : "Checking API"}
        </div>
        <div className="who">
          {list.length > 0 ? (
            <label className="who-select">
              <span className="who-label">Acting as</span>
              <select
                value={current?.user_id ?? ""}
                onChange={(e) => {
                  setUserId(e.target.value);
                  setSelection(null);
                }}
              >
                {firms.map((f) => (
                  <optgroup key={f} label={f}>
                    {list
                      .filter((i) => i.firm_name === f)
                      .map((i) => (
                        <option key={i.user_id} value={i.user_id}>
                          {i.name} · {i.role}
                        </option>
                      ))}
                  </optgroup>
                ))}
              </select>
            </label>
          ) : (
            <label className="who-select">
              <span className="who-label">{identities.loading ? "Loading identities" : "Paste a dev token"}</span>
              <input
                value={pasted}
                onChange={(e) => setPasted(e.target.value.trim())}
                placeholder="eyJhbGciOi…"
                spellCheck={false}
              />
            </label>
          )}
          {current && current.firm_name !== "Pasted token" && (
            <span className="who-firm">{current.firm_name}</span>
          )}
        </div>
      </header>

      {!current ? (
        <div className="boot">
          <h1>Choose who you are</h1>
          <p>
            The console signs every request with a dev token for a seeded user.{" "}
            {identities.error
              ? `Identities could not be loaded (${identities.error}). Run the seed script, check DATAVIV_ENV=dev, or paste a token from its output above.`
              : "Loading identities from the API…"}
          </p>
        </div>
      ) : view === "studio" ? (
        <DraftingStudio who={current} key={current.user_id} />
      ) : (
        <div className={`shell ${selection ? "" : "shell-wide"}`} key={current.user_id}>
          <MatterRail
            selected={matterId}
            onSelect={(id) => {
              setMatterId(id);
              setSelection(null);
            }}
            rev={mattersRev}
            onCreated={(id) => {
              setMattersRev((r) => r + 1);
              setMatterId(id);
            }}
          />
          <Workspace
            matterId={matterId}
            who={current}
            docsRev={docsRev}
            onDocsChanged={() => setDocsRev((r) => r + 1)}
            onInspect={setSelection}
            onClearMatter={() => setMatterId(null)}
            selection={selection}
          />
          {/* The inspector only takes room when something is selected. */}
          {selection && (
            <Inspector
              selection={selection}
              onInspect={setSelection}
              onDeleted={() => {
                setSelection(null);
                setDocsRev((r) => r + 1);
              }}
              docsRev={docsRev}
            />
          )}
        </div>
      )}
      <RequestLog />
    </div>
  );
}
