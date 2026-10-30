import { useState } from "react";
import { api, type Identity } from "../api";
import { useLoad } from "../hooks";
import type { Selection } from "./Inspector";
import { DocumentsTab } from "./DocumentsTab";
import { UploadTab } from "./UploadTab";
import { PeopleTab } from "./PeopleTab";
import { SearchTab } from "./SearchTab";
import { AssistantTab } from "./AssistantTab";
import { DraftsTab } from "./DraftsTab";
import { ReviewTab } from "./ReviewTab";
import { WorkflowsTab } from "./WorkflowsTab";
import { AgentsTab } from "./AgentsTab";
import { Empty, ErrorNote } from "./ui";

type Tab = "assistant" | "drafts" | "review" | "workflows" | "agents" | "documents" | "upload" | "people" | "search";

export function Workspace(props: {
  matterId: string | null;
  who: Identity;
  docsRev: number;
  onDocsChanged: () => void;
  onInspect: (s: Selection) => void;
  onClearMatter: () => void;
  selection: Selection;
}) {
  const [tab, setTab] = useState<Tab>(props.matterId ? "assistant" : "search");
  const [openDraft, setOpenDraft] = useState<string | null>(null);
  const awaiting = useLoad(() => api.awaitingMe(), [props.matterId, tab]);
  const toApprove = (awaiting.data ?? []).filter((x) => x.matter_id === props.matterId).length;
  const approvals = useLoad(() => api.approvals(true), [props.matterId, tab]);
  const here = (approvals.data ?? []).filter((x) => x.matter_id === props.matterId);
  const stepsToApprove = here.filter((x) => x.subject !== "agent").length;
  const actionsToApprove = here.filter((x) => x.subject === "agent").length;
  const matter = useLoad(props.matterId ? () => api.matter(props.matterId!) : null, [props.matterId]);
  const hasMatter = !!props.matterId && !!matter.data;
  const active: Tab = hasMatter ? tab : "search";

  const tabs: { id: Tab; name: string }[] = [
    { id: "assistant", name: "Assistant" },
    { id: "drafts", name: toApprove ? `Drafts · ${toApprove} to approve` : "Drafts" },
    { id: "review", name: "Review tables" },
    { id: "workflows", name: stepsToApprove ? `Workflows · ${stepsToApprove} to approve` : "Workflows" },
    { id: "agents", name: actionsToApprove ? `Agents · ${actionsToApprove} to approve` : "Agents" },
    { id: "documents", name: "Documents" },
    { id: "upload", name: "Upload" },
    { id: "people", name: "People" },
    { id: "search", name: "Search" },
  ];

  return (
    <main className="work">
      <div className="work-head">
        {matter.data ? (
          <>
            <h1 className="work-title">{matter.data.title}</h1>
            <p className="work-meta">
              {matter.data.client_name}
              {matter.data.forum && <> · {matter.data.forum}</>}
              {matter.data.my_role && (
                <>
                  {" "}
                  · you are <strong>{matter.data.my_role}</strong>
                </>
              )}
            </p>
          </>
        ) : (
          <h1 className="work-title">Search your matters</h1>
        )}
        <nav className="tabs" role="tablist">
          {tabs.map((t) => (
            <button
              key={t.id}
              role="tab"
              aria-selected={active === t.id}
              className={`tab ${active === t.id ? "is-on" : ""}`}
              disabled={t.id !== "search" && !hasMatter}
              onClick={() => setTab(t.id)}
            >
              {t.name}
            </button>
          ))}
        </nav>
      </div>

      <div className="work-body">
        {props.matterId && matter.status === 404 && (
          <Empty title={`${props.who.name} cannot see this matter`}>
            <p>
              The API answered <code>404 Not found</code> for matter <code className="id">{props.matterId.slice(0, 8)}</code>.
              Matters behind an ethical wall, in another firm, or without your membership look exactly like matters that
              do not exist, so nothing leaks (ADR-007).
            </p>
            <button className="btn" onClick={props.onClearMatter}>
              Clear selection
            </button>
          </Empty>
        )}
        {props.matterId && matter.error && matter.status !== 404 && <ErrorNote>{matter.error}</ErrorNote>}

        {active === "assistant" && hasMatter && (
          <AssistantTab
            key={props.matterId}
            matterId={props.matterId!}
            onInspect={props.onInspect}
            onOpenDraft={(id) => {
              setOpenDraft(id);
              setTab("drafts");
            }}
          />
        )}
        {active === "drafts" && hasMatter && (
          <DraftsTab key={props.matterId} matterId={props.matterId!} who={props.who} openDraftId={openDraft} onInspect={props.onInspect} />
        )}
        {active === "review" && hasMatter && (
          <ReviewTab key={props.matterId} matterId={props.matterId!} onInspect={props.onInspect} />
        )}
        {active === "workflows" && hasMatter && (
          <WorkflowsTab
            key={props.matterId}
            matterId={props.matterId!}
            canManage={props.who.role === "partner" || props.who.role === "admin"}
            onInspect={props.onInspect}
            onOpenDraft={(id) => {
              setOpenDraft(id);
              setTab("drafts");
            }}
          />
        )}
        {active === "agents" && hasMatter && (
          <AgentsTab
            key={props.matterId}
            matterId={props.matterId!}
            onInspect={props.onInspect}
            onOpenDraft={(id) => {
              setOpenDraft(id);
              setTab("drafts");
            }}
          />
        )}
        {active === "documents" && hasMatter && (
          <DocumentsTab
            matterId={props.matterId!}
            rev={props.docsRev}
            onInspect={props.onInspect}
            selectedDoc={props.selection?.docId ?? null}
          />
        )}
        {active === "upload" && hasMatter && (
          <UploadTab matterId={props.matterId!} onDone={props.onDocsChanged} onInspect={props.onInspect} />
        )}
        {active === "people" && hasMatter && <PeopleTab matterId={props.matterId!} who={props.who} />}
        {active === "search" && (
          <SearchTab
            matterId={hasMatter ? props.matterId : null}
            matterTitle={matter.data?.title ?? null}
            onInspect={props.onInspect}
            selectedChunk={props.selection?.kind === "chunk" ? props.selection.chunkId : null}
          />
        )}
      </div>
    </main>
  );
}
