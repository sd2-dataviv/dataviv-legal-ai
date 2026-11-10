// Typed client for the Dataviv AI OS backend. Every call is reported to the request log.

export type Identity = {
  firm_id: string;
  firm_name: string;
  user_id: string;
  name: string;
  email: string;
  role: string;
  access_token: string;
};

export type MatterRole = "lead" | "partner" | "member" | "viewer";

export type Matter = {
  id: string;
  title: string;
  client_name: string;
  matter_type: string;
  forum: string | null;
  cnr: string | null;
  practice_area: string | null;
  status: string;
  created_at: string;
  my_role: MatterRole | null;
};

export type MatterCreate = {
  title: string;
  client_name: string;
  matter_type: string;
  forum: string | null;
  cnr: string | null;
  practice_area: string | null;
};

export type DocStatus = "queued" | "processing" | "indexed" | "partial" | "failed";

export type Doc = {
  id: string;
  matter_id: string;
  title: string;
  doc_type: string;
  status: DocStatus;
  page_count: number | null;
  ocr_pending_pages: number[];
  error: string | null;
  current_version_id: string | null;
  created_at: string;
  updated_at: string;
};

export type Version = {
  id: string;
  version_no: number;
  original_filename: string;
  mime: string;
  size_bytes: number;
  sha256: string;
  created_at: string;
};

export type DocDetail = Doc & { versions: Version[]; chunk_count: number };

export type Chunk = {
  id: string;
  document_id: string;
  version_id: string;
  ordinal: number;
  section_path: string | null;
  page_from: number;
  page_to: number;
  char_start: number;
  char_end: number;
  text: string;
  ocr_confidence: number | null;
};

export type UploadedFile = {
  filename: string;
  status: "queued" | "new_version" | "duplicate" | "rejected";
  document_id: string | null;
  version_id: string | null;
  version_no: number | null;
  reason: string | null;
};

export type UploadResult = { job_id: string | null; files: UploadedFile[] };

export type Job = {
  id: string;
  kind: string;
  status: "queued" | "running" | "succeeded" | "failed";
  progress: number;
  done: number;
  total: number;
  current_step: string | null;
  error: string | null;
};

export type SearchHit = {
  chunk_id: string;
  document_id: string;
  document_title: string;
  matter_id: string;
  doc_type: string;
  section_path: string | null;
  page_from: number;
  page_to: number;
  snippet: string;
  score: number;
  match: "keyword" | "semantic" | "both";
  similarity: number | null;
};

export type SearchMode = "hybrid" | "keyword" | "semantic";

export type SearchResponse = { query: string; mode: SearchMode; notice: string | null; hits: SearchHit[] };

export type SearchStatus = {
  enabled: boolean;
  embeddings_backend: string;
  vector_store: string;
  model: string | null;
  collection: string | null;
  reachable: boolean | null;
  vectors: number | null;
  pending_passages: number | null;
  error: string | null;
};

export type FirmUser = { id: string; name: string; email: string; role: string };

export type MemberDetail = {
  user_id: string;
  name: string;
  email: string;
  firm_role: string;
  matter_role: MatterRole;
};

export type Wall = { user_id: string; name: string; reason: string; created_at: string };

export type MatterPeople = { members: MemberDetail[]; walls: Wall[] };

export const DOC_TYPES = [
  "petition",
  "written_statement",
  "order",
  "judgment",
  "agreement",
  "notice",
  "affidavit",
  "correspondence",
  "other",
] as const;


export type Mode = "ask" | "summarise" | "draft";
export type DraftKind = "notice" | "reply" | "email" | "memo";
export type Label = "supported" | "partial" | "unsupported" | "uncited";

export type Thread = {
  id: string;
  matter_id: string;
  title: string;
  created_by: string;
  created_at: string;
  updated_at: string;
  message_count: number;
};

export type AnswerSource = {
  key: string;
  chunk_id: string;
  document_id: string;
  document_title: string;
  section_path: string | null;
  page_from: number;
  page_to: number;
  excerpt: string;
};

export type AnswerSentence = { text: string; cites: string[]; block: number; prefix: string; label: Label; reason: string };

export type ChatMessage = {
  id: string;
  thread_id: string;
  role: "user" | "assistant";
  content: string;
  mode: Mode;
  options: { draft_kind?: DraftKind | null; document_ids?: string[]; requested_model?: string };
  sources: AnswerSource[];
  sentences: AnswerSentence[];
  verification: Partial<Record<Label, number>> & { verifier?: string; note?: string };
  status: "streaming" | "complete" | "failed";
  error: string | null;
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  latency_ms: number | null;
  cost_usd: number | null;
  created_at: string;
};

export type ThreadDetail = Thread & { messages: ChatMessage[] };

export type AssistantModels = {
  backend: string;
  default: string;
  allowed: string[];
  routes: Record<string, string>;
  catalogue: string[];
};

export type AskBody = {
  content: string;
  mode: Mode;
  draft_kind?: DraftKind | null;
  document_ids?: string[] | null;
  model?: string;
};

export type AskEvent =
  | { type: "meta"; data: { message_id: string; question_id: string; model: string | null; sources: AnswerSource[]; terms: string[] } }
  | { type: "delta"; data: { text: string } }
  | { type: "notice"; data: { text: string } }
  | { type: "done"; data: ChatMessage }
  | { type: "error"; data: { detail: string; status: number | null; retry_after: number | null; message?: ChatMessage } };


export type DraftKind2 = "notice" | "reply" | "email" | "memo" | "other";
export type DraftStatus = "draft" | "in_review" | "changes_requested" | "approved";
export type Person = { id: string; name: string };

export type DraftSummary = {
  id: string;
  matter_id: string;
  title: string;
  kind: DraftKind2;
  status: DraftStatus;
  current_version_no: number;
  approved_version_no: number | null;
  updated_by: Person;
  updated_at: string;
  reviewer: Person | null;
};

export type DraftVersionInfo = {
  version_no: number;
  note: string | null;
  created_by: Person;
  created_at: string;
  chars: number;
  contributors: Person[];
};
export type DiffOp = { op: "equal" | "insert" | "delete"; text: string };
export type Suggestion = {
  id: string;
  requested_by: Person;
  instruction: string;
  scope: "selection" | "draft";
  quote: string;
  replacement: string;
  explanation: string;
  diff: DiffOp[];
  sources: (DraftSource & { excerpt?: string })[];
  warnings: string[];
  status: "pending" | "accepted" | "rejected" | "stale";
  decided_by: Person | null;
  decided_at: string | null;
  model: string | null;
  cost_usd: number;
  created_at: string;
};
export type LiveInfo = { available: boolean; unsaved_changes: boolean; contributors: Person[] };
export type DraftReview = {
  id: string;
  version_no: number;
  status: "pending" | "approved" | "changes_requested" | "withdrawn";
  requested_by: Person;
  reviewer: Person;
  message: string | null;
  decision_note: string | null;
  decided_at: string | null;
  created_at: string;
};
export type DraftComment = {
  id: string;
  version_no: number;
  author: Person;
  body: string;
  quote: string | null;
  resolved_by: Person | null;
  resolved_at: string | null;
  created_at: string;
};
export type Presence = { user: Person; editing: boolean; seen_at: string };
export type DraftSource = {
  n: number;
  chunk_id: string;
  document_id: string;
  document_title: string;
  section_path: string | null;
  page_from: number;
  page_to: number;
};

export type DraftDetail = DraftSummary & {
  content: string;
  sources: DraftSource[];
  approved_by: Person | null;
  approved_at: string | null;
  created_by: Person;
  created_at: string;
  my_matter_role: string;
  can_edit: boolean;
  versions: DraftVersionInfo[];
  reviews: DraftReview[];
  comments: DraftComment[];
  present: Presence[];
  activity: { action: string; actor: Person | null; detail: Record<string, unknown>; at: string }[];
  live: LiveInfo;
  suggestions: Suggestion[];
  signatures: SignatureRequest[];
};

// E-signature (Step 2e)
export type SignatureStatus = "sent" | "delivered" | "completed" | "declined" | "voided" | "failed";
export type SignerStatus = "created" | "sent" | "delivered" | "signed" | "declined" | "voided";
export type Signer = {
  name: string;
  email: string;
  routing_order: number;
  status: SignerStatus;
  signed_at: string | null;
  declined_reason: string | null;
  user: Person | null;
};
export type SignatureRequest = {
  id: string;
  draft_id: string;
  version_no: number;
  provider: "docusign" | "fake";
  envelope_id: string;
  status: SignatureStatus;
  subject: string;
  message: string | null;
  sent_by: Person;
  sent_at: string;
  completed_at: string | null;
  voided_reason: string | null;
  last_checked_at: string | null;
  last_error: string | null;
  signed_document_id: string | null;
  certificate_document_id: string | null;
  signers: Signer[];
};
export type ESignStatus = {
  backend: "none" | "fake" | "docusign";
  ready: boolean;
  environment: "test" | "sandbox" | "production" | null;
  account_name: string | null;
  detail: string | null;
  consent_url: string | null;
  callbacks: boolean;
};
export type SignerInput = { name: string; email: string; routing_order: number };

export type DraftHead = {
  current_version_no: number;
  status: DraftStatus;
  updated_by: Person;
  updated_at: string;
  present: Presence[];
};

export type DiffResult = {
  from_version: number;
  to_version: number;
  ops: { op: "equal" | "insert" | "delete"; text: string }[];
  inserted_words: number;
  deleted_words: number;
};


export type AnswerType = "text" | "yes_no" | "date" | "amount" | "choice";
export type CellStatus = "empty" | "queued" | "done" | "not_found" | "failed";
export type ReviewJob = { id: string; status: string; done: number; total: number; progress: number; current_step: string | null; error: string | null };
export type ReviewColumn = { id: string; position: number; label: string; question: string; answer_type: AnswerType; choices: string[] };
export type ReviewRow = { document_id: string; title: string; doc_type: string; status: string; page_count: number | null };
export type ReviewCell = {
  id: string;
  column_id: string;
  document_id: string;
  status: CellStatus;
  answer: string | null;
  model_answer: string | null;
  value: string | null;
  quote: string | null;
  chunk_id: string | null;
  page_from: number | null;
  page_to: number | null;
  verified: boolean | null;
  flag: string | null;
  error: string | null;
  edited_by: Person | null;
  reviewed_by: Person | null;
};
export type ReviewSummary = {
  id: string;
  matter_id: string;
  title: string;
  columns: number;
  documents: number;
  updated_at: string;
  created_by: Person;
  job: ReviewJob | null;
};
export type ReviewDetail = ReviewSummary & {
  my_matter_role: string;
  can_edit: boolean;
  column_list: ReviewColumn[];
  rows: ReviewRow[];
  cells: ReviewCell[];
  totals: {
    cells: number;
    done: number;
    not_found: number;
    failed: number;
    queued: number;
    unverified: number;
    reviewed: number;
    edited: number;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
  };
};
export type ReviewTemplate = { id: string; title: string; description: string; columns: Omit<ReviewColumn, "id" | "position">[] };
export type ColumnInput = { label: string; question: string; answer_type: AnswerType; choices: string[] };

// ---------------------------------------------------------------- workflows (Step 4)

export type WorkflowInput = {
  key: string;
  label: string;
  type: "text" | "textarea" | "number" | "choice" | "document" | "documents" | "playbook";
  required?: boolean;
  default?: unknown;
  choices?: string[];
  min?: number;
  max?: number;
};
export type WorkflowSummary = {
  slug: string;
  version: number;
  name: string;
  description: string;
  builtin: boolean;
  inputs: WorkflowInput[];
  steps: { id: string; type: string; title: string }[];
};
export type RunStatus = "queued" | "running" | "waiting_approval" | "succeeded" | "failed" | "rejected" | "cancelled";
export type StepStatus = "pending" | "running" | "waiting" | "done" | "failed" | "skipped";
export type WorkflowStep = {
  id: string;
  position: number;
  key: string;
  type: string;
  title: string;
  status: StepStatus;
  resolved: Record<string, unknown>;
  output: Record<string, any>; // shape depends on the step type
  model: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  error: string | null;
  attempts: number;
  started_at: string | null;
  finished_at: string | null;
};
export type Approval = {
  id: string;
  run_id: string;
  matter_id: string;
  step_id: string;
  required_role: "matter_editor" | "matter_manager" | "firm_partner";
  message: string | null;
  status: "pending" | "approved" | "rejected" | "cancelled";
  requested_by: Person;
  decided_by: Person | null;
  decided_at: string | null;
  comment: string | null;
  created_at: string;
  can_decide: boolean;
  run_name: string | null;
  subject: "workflow" | "agent";
  detail: { tool?: string; arguments?: Record<string, unknown>; thought?: string | null };
};
export type RunSummary = {
  id: string;
  matter_id: string;
  slug: string;
  version: number;
  name: string;
  status: RunStatus;
  created_by: Person;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
  cost_usd: number;
  error: string | null;
  steps_done: number;
  steps_total: number;
};
export type RunDetail = RunSummary & {
  inputs: Record<string, unknown>;
  input_tokens: number;
  output_tokens: number;
  steps: WorkflowStep[];
  approvals: Approval[];
  job: { id: string; status: string; done: number; total: number; progress: number; current_step: string | null } | null;
  can_cancel: boolean;
  can_retry: boolean;
};
export type Clause = {
  key: string;
  title: string;
  question: string;
  preferred: string;
  fallback: string;
  unacceptable: string;
  guidance: string;
};
export type PlaybookInput = { name: string; contract_type: string; description: string | null; clauses: Clause[] };
export type Playbook = PlaybookInput & { id: string; version: number; updated_by: Person; updated_at: string };
export type PlaybookTemplate = PlaybookInput & { id: string };

// ---------------------------------------------------------------- agents (Step 5)

export type AgentTool = { name: string; policy: "auto" | "confirm" | "manager" };
export type AgentSummary = {
  slug: string;
  version: number;
  name: string;
  description: string;
  builtin: boolean;
  goal_hint: string;
  tools: AgentTool[];
  max_steps: number;
  max_cost_usd: number;
};
export type AgentRunStatus =
  | "queued"
  | "running"
  | "waiting_approval"
  | "waiting_subagents"
  | "succeeded"
  | "stopped"
  | "failed"
  | "cancelled";
export type AgentStep = {
  id: string;
  position: number;
  thought: string | null;
  tool: string;
  arguments: Record<string, unknown>;
  status: "running" | "waiting_approval" | "approved" | "waiting" | "done" | "rejected" | "failed";
  observation: string | null;
  result: Record<string, any>;
  model: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  error: string | null;
  created_at: string;
  finished_at: string | null;
};
export type AgentRunSummary = {
  id: string;
  matter_id: string;
  slug: string;
  version: number;
  name: string;
  goal: string;
  status: AgentRunStatus;
  created_by: Person;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
  steps_used: number;
  max_steps: number;
  cost_usd: number;
  max_cost_usd: number;
  error: string | null;
};
export type AgentSource = {
  key: string;
  chunk_id: string;
  document_id: string;
  document_title: string;
  section_path: string | null;
  page_from: number;
  page_to: number;
  excerpt: string;
};
export type AgentRunDetail = AgentRunSummary & {
  parent_run_id: string | null;
  scope_document_ids: string[] | null;
  tools: AgentTool[];
  input_tokens: number;
  output_tokens: number;
  answer: string | null;
  sentences: { text: string; cites: string[]; block: number; prefix: string; label: string; reason: string }[];
  sources: AgentSource[];
  verification: Record<string, any>;
  steps: AgentStep[];
  approvals: Approval[];
  children: { id: string; name: string; goal: string; status: AgentRunStatus; steps_used: number; cost_usd: number; answer: string | null }[];
  job: RunDetail["job"];
  can_cancel: boolean;
};
export type MemoryKind = "fact" | "event" | "party" | "deadline" | "note";
export type MemoryItem = {
  id: string;
  matter_id: string;
  kind: MemoryKind;
  subject: string;
  content: string;
  event_date: string | null;
  document_id: string | null;
  document_title: string | null;
  chunk_id: string | null;
  page_from: number | null;
  page_to: number | null;
  source: "user" | "agent";
  agent_run_id: string | null;
  created_by: Person;
  created_at: string;
};

// ---------------------------------------------------------------- request log

export type LogEntry = {
  id: number;
  at: Date;
  method: string;
  path: string;
  status: number | null; // null = network failure
  ms: number;
  detail?: string; // problem+json or validation detail, pretty-printed
  who: string;
};

type Listener = (e: LogEntry) => void;
const listeners = new Set<Listener>();
let seq = 0;

export function onRequest(fn: Listener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// ---------------------------------------------------------------- auth

let token: string | null = null;
let who = "anonymous";

export function setIdentity(accessToken: string | null, label: string): void {
  token = accessToken;
  who = label;
}

/** The live editor sends the token as a WebSocket sub-protocol (browsers cannot set headers there). */
export function currentToken(): string | null {
  return token;
}

// ---------------------------------------------------------------- errors

export class ApiError extends Error {
  constructor(
    public status: number,
    public title: string,
    public detail: string,
  ) {
    super(detail ? `${title}: ${detail}` : title);
  }
}

/** FastAPI 422 bodies carry a list of {loc, msg}; problem+json carries {title, detail}. */
function describe(body: unknown): { title: string; detail: string } {
  if (body && typeof body === "object") {
    const b = body as Record<string, unknown>;
    if (Array.isArray(b.detail)) {
      const parts = b.detail.map((d) => {
        const e = d as { loc?: unknown[]; msg?: string };
        const loc = (e.loc ?? []).filter((x) => x !== "body").join(".");
        return loc ? `${loc}: ${e.msg}` : String(e.msg);
      });
      return { title: "Validation failed", detail: parts.join("; ") };
    }
    return { title: String(b.title ?? "Request failed"), detail: String(b.detail ?? "") };
  }
  return { title: "Request failed", detail: typeof body === "string" ? body : "" };
}

// ---------------------------------------------------------------- core fetch

export async function send(method: string, path: string, body?: unknown): Promise<Response> {
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  let payload: BodyInit | undefined;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }

  const started = performance.now();
  const entry: LogEntry = { id: ++seq, at: new Date(), method, path, status: null, ms: 0, who };
  let res: Response;
  try {
    res = await fetch(path, { method, headers, body: payload });
  } catch (err) {
    entry.ms = Math.round(performance.now() - started);
    entry.detail = `Network error: ${(err as Error).message}. Is the API running? (scripts\\windows\\console.bat prints its address.)`;
    listeners.forEach((l) => l(entry));
    throw new ApiError(0, "API unreachable", "Start the backend, then retry.");
  }
  entry.ms = Math.round(performance.now() - started);
  entry.status = res.status;

  if (!res.ok) {
    const text = await res.text();
    let parsed: unknown = text;
    try {
      parsed = JSON.parse(text);
    } catch {
      /* not JSON */
    }
    entry.detail = typeof parsed === "string" ? parsed : JSON.stringify(parsed, null, 2);
    listeners.forEach((l) => l(entry));
    const { title, detail } = describe(parsed);
    throw new ApiError(res.status, title, detail);
  }
  listeners.forEach((l) => l(entry));
  return res;
}

async function json<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await send(method, path, body);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ---------------------------------------------------------------- endpoints

export const api = {
  health: () => json<{ status: string; env: string }>("GET", "/health"),
  identities: () => json<Identity[]>("GET", "/v1/dev/identities"),
  me: () => json<{ user_id: string; firm_id: string; role: string; email: string }>("GET", "/v1/me"),
  users: () => json<FirmUser[]>("GET", "/v1/users"),

  matters: () => json<Matter[]>("GET", "/v1/matters"),
  matter: (id: string) => json<Matter>("GET", `/v1/matters/${id}`),
  createMatter: (body: MatterCreate) => json<Matter>("POST", "/v1/matters", body),
  people: (id: string) => json<MatterPeople>("GET", `/v1/matters/${id}/people`),
  addMember: (id: string, user_id: string, matter_role: MatterRole) =>
    json<{ user_id: string; matter_role: MatterRole }>("POST", `/v1/matters/${id}/members`, { user_id, matter_role }),
  raiseWall: (id: string, user_id: string, reason: string) =>
    json<{ id: string }>("POST", `/v1/matters/${id}/walls`, { user_id, reason }),

  documents: (matterId: string, filters: { doc_type?: string; status?: string } = {}) => {
    const q = new URLSearchParams();
    if (filters.doc_type) q.set("doc_type", filters.doc_type);
    if (filters.status) q.set("status", filters.status);
    const qs = q.toString();
    return json<Doc[]>("GET", `/v1/matters/${matterId}/documents${qs ? `?${qs}` : ""}`);
  },
  document: (id: string) => json<DocDetail>("GET", `/v1/documents/${id}`),
  chunk: (docId: string, chunkId: string) => json<Chunk>("GET", `/v1/documents/${docId}/chunks/${chunkId}`),
  deleteDocument: (id: string) => json<void>("DELETE", `/v1/documents/${id}`),
  upload: (matterId: string, files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f, f.name));
    return json<UploadResult>("POST", `/v1/matters/${matterId}/documents`, form);
  },
  job: (id: string) => json<Job>("GET", `/v1/jobs/${id}`),

  search: (body: {
    query: string;
    matter_ids?: string[] | null;
    doc_types?: string[] | null;
    limit?: number;
    mode?: SearchMode;
  }) => json<SearchResponse>("POST", "/v1/search", body),
  searchStatus: () => json<SearchStatus>("GET", "/v1/search/status"),

  threads: (matterId: string, q?: string) =>
    json<Thread[]>("GET", `/v1/matters/${matterId}/threads${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  createThread: (matterId: string, title?: string) =>
    json<Thread>("POST", `/v1/matters/${matterId}/threads`, title ? { title } : {}),
  thread: (id: string) => json<ThreadDetail>("GET", `/v1/threads/${id}`),
  models: () => json<AssistantModels>("GET", "/v1/assistant/models"),

  /** POST a question and read the Server-Sent Events stream as it arrives. */
  async ask(threadId: string, body: AskBody, onEvent: (e: AskEvent) => void): Promise<void> {
    const res = await send("POST", `/v1/threads/${threadId}/messages`, body);
    if (!res.body) throw new ApiError(0, "No stream", "The browser did not expose the response stream.");
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (value) buffer += decoder.decode(value, { stream: true });
      let cut: number;
      while ((cut = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        let type = "message";
        let data = "";
        for (const line of block.split("\n")) {
          if (line.startsWith("event: ")) type = line.slice(7);
          else if (line.startsWith("data: ")) data += line.slice(6);
        }
        if (data) onEvent({ type, data: JSON.parse(data) } as AskEvent);
      }
      if (done) break;
    }
  },

  drafts: (matterId: string) => json<DraftSummary[]>("GET", `/v1/matters/${matterId}/drafts`),
  awaitingMe: () => json<DraftSummary[]>("GET", "/v1/drafts/awaiting-me"),
  createDraft: (matterId: string, body: { title?: string; kind?: DraftKind2; content?: string; from_message_id?: string }) =>
    json<DraftDetail>("POST", `/v1/matters/${matterId}/drafts`, body),
  draft: (id: string) => json<DraftDetail>("GET", `/v1/drafts/${id}`),
  updateDraft: (id: string, body: { title?: string; kind?: DraftKind2 }) => json<DraftDetail>("PATCH", `/v1/drafts/${id}`, body),
  saveVersion: (id: string, body: { content: string; base_version_no: number; note?: string; force?: boolean }) =>
    json<DraftVersionInfo>("POST", `/v1/drafts/${id}/versions`, body),
  draftVersion: (id: string, n: number) => json<DraftVersionInfo & { content: string }>("GET", `/v1/drafts/${id}/versions/${n}`),
  restoreVersion: (id: string, n: number) => json<DraftVersionInfo>("POST", `/v1/drafts/${id}/versions/${n}/restore`),
  diff: (id: string, from: number, to: number) => json<DiffResult>("GET", `/v1/drafts/${id}/diff?from=${from}&to=${to}`),
  requestReview: (id: string, reviewer_id: string, message?: string) =>
    json<DraftDetail>("POST", `/v1/drafts/${id}/review`, { reviewer_id, message }),
  decide: (id: string, decision: "approve" | "request_changes", note?: string) =>
    json<DraftDetail>("POST", `/v1/drafts/${id}/review/decision`, { decision, note }),
  withdraw: (id: string) => json<DraftDetail>("POST", `/v1/drafts/${id}/review/withdraw`),
  comment: (id: string, body: string, quote?: string | null) =>
    json<DraftDetail>("POST", `/v1/drafts/${id}/comments`, { body, quote }),
  resolveComment: (id: string, commentId: string) => json<DraftDetail>("POST", `/v1/drafts/${id}/comments/${commentId}/resolve`),
  heartbeat: (id: string, editing: boolean) => json<DraftHead>("POST", `/v1/drafts/${id}/presence`, { editing }),
  liveSave: (id: string, note?: string) => json<DraftVersionInfo>("POST", `/v1/drafts/${id}/live/save`, { note }),
  suggest: (
    id: string,
    body: { instruction: string; selection?: { text: string; start: number } | null; use_documents: boolean },
  ) => json<Suggestion>("POST", `/v1/drafts/${id}/suggestions`, body),
  acceptSuggestion: (id: string, sid: string) => json<Suggestion>("POST", `/v1/drafts/${id}/suggestions/${sid}/accept`),
  rejectSuggestion: (id: string, sid: string) => json<Suggestion>("POST", `/v1/drafts/${id}/suggestions/${sid}/reject`),
  exportDraft: (id: string, version?: number) =>
    saveFrom(`/v1/drafts/${id}/export${version ? `?version=${version}` : ""}`, "draft.docx"),
  esignStatus: () => json<ESignStatus>("GET", "/v1/esign/status"),
  sendForSignature: (
    id: string,
    body: { signers: SignerInput[]; subject?: string | null; message?: string | null; confirm_not_excluded: boolean },
  ) => json<SignatureRequest>("POST", `/v1/drafts/${id}/signatures`, body),
  refreshSignature: (rid: string) => json<SignatureRequest>("POST", `/v1/signatures/${rid}/refresh`),
  voidSignature: (rid: string, reason: string) => json<SignatureRequest>("POST", `/v1/signatures/${rid}/void`, { reason }),
  simulateSigning: (rid: string, action: "deliver" | "sign_next" | "sign_all" | "decline", reason?: string) =>
    json<SignatureRequest>("POST", `/v1/dev/signatures/${rid}/simulate`, { action, reason }),

  reviewTemplates: () => json<ReviewTemplate[]>("GET", "/v1/review-templates"),
  reviewTables: (matterId: string) => json<ReviewSummary[]>("GET", `/v1/matters/${matterId}/review-tables`),
  createReview: (
    matterId: string,
    body: { title?: string; template?: string | null; columns?: ColumnInput[]; document_ids?: string[] | null },
  ) => json<ReviewDetail>("POST", `/v1/matters/${matterId}/review-tables`, body),
  review: (id: string) => json<ReviewDetail>("GET", `/v1/review-tables/${id}`),
  renameReview: (id: string, title: string) => json<ReviewDetail>("PATCH", `/v1/review-tables/${id}`, { title }),
  deleteReview: (id: string) => json<void>("DELETE", `/v1/review-tables/${id}`),
  addColumn: (id: string, body: ColumnInput) => json<ReviewDetail>("POST", `/v1/review-tables/${id}/columns`, body),
  patchColumn: (id: string, columnId: string, body: Partial<ColumnInput>) =>
    json<ReviewDetail>("PATCH", `/v1/review-tables/${id}/columns/${columnId}`, body),
  deleteColumn: (id: string, columnId: string) => json<ReviewDetail>("DELETE", `/v1/review-tables/${id}/columns/${columnId}`),
  addReviewDocuments: (id: string, document_ids: string[]) =>
    json<ReviewDetail>("POST", `/v1/review-tables/${id}/documents`, { document_ids }),
  removeReviewDocument: (id: string, documentId: string) =>
    json<ReviewDetail>("DELETE", `/v1/review-tables/${id}/documents/${documentId}`),
  runReview: (id: string, scope: "missing" | "all") =>
    json<{ job_id: string | null; cells_queued: number; documents: number }>("POST", `/v1/review-tables/${id}/run`, { scope }),
  cancelReview: (id: string) => json<ReviewDetail>("POST", `/v1/review-tables/${id}/cancel`),
  patchCell: (id: string, cellId: string, body: { answer?: string; revert?: boolean; reviewed?: boolean }) =>
    json<ReviewDetail>("PATCH", `/v1/review-tables/${id}/cells/${cellId}`, body),
  exportReview: (id: string, format: "xlsx" | "csv") => saveFrom(`/v1/review-tables/${id}/export?format=${format}`, `review.${format}`),

  exportAnswer: (threadId: string, messageId: string) =>
    saveFrom(`/v1/threads/${threadId}/messages/${messageId}/export`, "answer.docx"),

  /** Downloads go through fetch so the bearer token is sent; the browser then saves the blob. */
  workflows: () => json<WorkflowSummary[]>("GET", "/v1/workflows"),
  startRun: (matterId: string, workflow: string, inputs: Record<string, unknown>) =>
    json<RunDetail>("POST", `/v1/matters/${matterId}/workflow-runs`, { workflow, inputs }),
  runs: (matterId: string) => json<RunSummary[]>("GET", `/v1/matters/${matterId}/workflow-runs`),
  run: (id: string) => json<RunDetail>("GET", `/v1/workflow-runs/${id}`),
  cancelRun: (id: string) => json<RunDetail>("POST", `/v1/workflow-runs/${id}/cancel`),
  retryRun: (id: string) => json<RunDetail>("POST", `/v1/workflow-runs/${id}/retry`),
  approvals: (mine: boolean) => json<Approval[]>("GET", `/v1/approvals?mine=${mine}`),
  decideApproval: (id: string, decision: "approve" | "reject", comment?: string) =>
    json<RunDetail | AgentRunDetail>("POST", `/v1/approvals/${id}/decision`, { decision, comment }),
  playbooks: () => json<Playbook[]>("GET", "/v1/playbooks"),
  playbookTemplates: () => json<PlaybookTemplate[]>("GET", "/v1/playbook-templates"),
  createPlaybook: (body: { from_template?: string; playbook?: PlaybookInput }) => json<Playbook>("POST", "/v1/playbooks", body),
  updatePlaybook: (id: string, body: PlaybookInput) => json<Playbook>("PUT", `/v1/playbooks/${id}`, body),
  deletePlaybook: (id: string) => json<void>("DELETE", `/v1/playbooks/${id}`),
  agents: () => json<AgentSummary[]>("GET", "/v1/agents"),
  startAgent: (
    matterId: string,
    body: { agent: string; goal: string; document_ids?: string[] | null; max_steps?: number; max_cost_usd?: number },
  ) => json<AgentRunDetail>("POST", `/v1/matters/${matterId}/agent-runs`, body),
  agentRuns: (matterId: string) => json<AgentRunSummary[]>("GET", `/v1/matters/${matterId}/agent-runs`),
  agentRun: (id: string) => json<AgentRunDetail>("GET", `/v1/agent-runs/${id}`),
  cancelAgent: (id: string) => json<AgentRunDetail>("POST", `/v1/agent-runs/${id}/cancel`),
  memory: (matterId: string, q?: string) =>
    json<MemoryItem[]>("GET", `/v1/matters/${matterId}/memory${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  addMemory: (matterId: string, body: { kind: MemoryKind; subject: string; content: string; event_date?: string | null }) =>
    json<MemoryItem>("POST", `/v1/matters/${matterId}/memory`, body),
  deleteMemory: (id: string) => json<void>("DELETE", `/v1/memory/${id}`),
  download: (docId: string, fallbackName: string) => saveFrom(`/v1/documents/${docId}/download`, fallbackName),
};

export async function saveFrom(path: string, fallbackName: string): Promise<void> {
  const res = await send("GET", path);
  const cd = res.headers.get("Content-Disposition") ?? "";
  const match = /filename\*=UTF-8''([^;]+)/i.exec(cd);
  const name = match ? decodeURIComponent(match[1]) : fallbackName;
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}


// ---------------------------------------------------------------- helpers

export function errorText(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return err instanceof Error ? err.message : String(err);
}

export function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function when(iso: string): string {
  return new Date(iso).toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function label(s: string): string {
  const t = s.replace(/_/g, " ");
  return t.charAt(0).toUpperCase() + t.slice(1);
}
