// Drafting studio add-on: typed client for /v1/studio. Uses the shared request helper, so every call
// shows in the request log with the current identity's token.

import { saveFrom, send, ApiError } from "./api";

export type StudioField = { key: string; label: string; hint: string; required: boolean; long: boolean };

export type StudioTemplate = {
  key: string;
  category: string;
  name: string;
  description: string;
  fields: StudioField[];
};

export type StudioStatus = "streaming" | "complete" | "failed";
export type StudioPerson = { id: string; name: string };

export type StudioDoc = {
  id: string;
  family_id: string;
  parent_id: string | null;
  template: string;
  template_name: string;
  category: string;
  title: string;
  summary: string;
  status: StudioStatus;
  error: string | null;
  model: string | null;
  created_by: StudioPerson;
  created_at: string;
  updated_at: string;
  versions: number;
};

export type StudioRevision = {
  id: string;
  parent_id: string | null;
  instructions: string;
  status: StudioStatus;
  created_by: StudioPerson;
  created_at: string;
};

export type StudioDetail = StudioDoc & {
  inputs: Record<string, string>;
  instructions: string;
  content: string;
  input_tokens: number | null;
  output_tokens: number | null;
  latency_ms: number | null;
  cost_usd: string | number | null;
  revisions: StudioRevision[];
};

export type GenerateBody = {
  template: string;
  title?: string;
  inputs: Record<string, string>;
  instructions: string;
  model?: string | null;
};

export type StudioEvent =
  | {
      type: "meta";
      data: { document_id: string; family_id: string; parent_id: string | null; template: string; title: string; model: string };
    }
  | { type: "delta"; data: { text: string } }
  | { type: "notice"; data: { text: string } }
  | { type: "done"; data: StudioDetail }
  | { type: "error"; data: { detail: string; status: number; retry_after: number | null; document: StudioDetail } };

async function json<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await send(method, path, body);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

async function readStream(res: Response, onEvent: (e: StudioEvent) => void): Promise<void> {
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
      if (data) onEvent({ type, data: JSON.parse(data) } as StudioEvent);
    }
    if (done) break;
  }
}

export const studio = {
  templates: () => json<StudioTemplate[]>("GET", "/v1/studio/templates"),
  documents: (filters: { q?: string; template?: string; mine?: boolean } = {}) => {
    const q = new URLSearchParams();
    if (filters.q) q.set("q", filters.q);
    if (filters.template) q.set("template", filters.template);
    if (filters.mine) q.set("mine", "true");
    const qs = q.toString();
    return json<StudioDoc[]>("GET", `/v1/studio/documents${qs ? `?${qs}` : ""}`);
  },
  document: (id: string) => json<StudioDetail>("GET", `/v1/studio/documents/${id}`),
  update: (id: string, body: { title?: string; content?: string }) =>
    json<StudioDetail>("PATCH", `/v1/studio/documents/${id}`, body),
  remove: (id: string) => json<void>("DELETE", `/v1/studio/documents/${id}`),
  exportWord: (id: string) => saveFrom(`/v1/studio/documents/${id}/export`, "document.docx"),

  async generate(body: GenerateBody, onEvent: (e: StudioEvent) => void): Promise<void> {
    await readStream(await send("POST", "/v1/studio/documents", body), onEvent);
  },
  async revise(id: string, instructions: string, onEvent: (e: StudioEvent) => void): Promise<void> {
    await readStream(await send("POST", `/v1/studio/documents/${id}/revise`, { instructions }), onEvent);
  },
};
