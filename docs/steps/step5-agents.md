# Step 5 — Agents

**Implements:** AGT-01..07 (AGT-08, the no-code Agent Builder, is Phase 3; firms can already publish agents as JSON) · **Status:** done, 12 October 2026 — 115/115 tests on Windows, mypy clean; live run on Groq `openai/gpt-oss-120b` (4 steps, $0.00096, corrected its own invalid arguments)

An agent is given a goal and works through a matter's documents step by step: it searches, reads, saves what it finds and drafts, as the person who started it and never beyond their access. Every step is shown, costed and audited; the actions its policy marks wait for a person.

## Built-in agents (AGT-01)

| Agent | Tools | Writes and who approves | Budget |
|---|---|---|---|
| Matter researcher | recall memory, list, search, read passage, read document | none (read-only) | 8 steps, $0.03 |
| Chronology builder | the above + save to memory, create draft | memory: at once; draft: anyone who can edit the matter confirms | 14 steps, $0.06 |
| Reply to a legal notice | read tools + create draft | draft: the matter lead or a partner, not the starter | 12 steps, $0.06 |
| Due diligence across documents | list, hand to subagents, save to memory | memory: at once | 8 steps, $0.12 |
| Document analyst (subagent only) | read document, search, read passage | none | 5 steps, $0.02 |

A definition is `instructions` + `tools` + `approval` (per write tool: `auto`, `confirm` or `manager`) + `budget` (`max_steps`, `max_cost_usd`) + optional `subagent`. A run may lower the budget, never raise it. Partners and admins publish firm agents with `POST /v1/agents`; re-publishing a slug adds a version; runs keep a snapshot of the definition they started with.

## The loop (AGT-02) — ADR-018

Each step, the model returns one strict-JSON decision `{thought, tool, arguments}`. The runtime:

1. checks the tool is one this agent may use and validates the arguments against the tool's Pydantic model (a mistake becomes an observation the agent can correct, and still costs a step);
2. runs read tools, and `auto` write tools, at once; for `confirm` / `manager` write tools it creates an approval and the run waits;
3. records the observation, tokens and cost on the step, and re-queues itself (one step per job, as for workflows, so uploads are never stuck behind an agent).

On its last step the agent may only call `final_answer`; a run that runs out of steps or money stops with a clear reason. The final answer is checked by the Assistant's sentence verifier against the passages the run actually read. Observations are quoted as data, and the agent is told to ignore instructions inside documents; even if it did not, it has no tool that reaches outside the matter, and its writes go through the approval policy.

## Acting as the user (AGT-04)

- Tools take no matter id. Passages are addressed by the ids the run has seen (S1, S2…), never by raw database ids; documents are checked to be in the matter (and, for a subagent, its one document).
- Tool arguments forbid extra fields, so an agent cannot add a parameter to widen its scope.
- Before every step the starting user's access is checked again: remove them from the matter and the agent fails at its next step.
- Viewers cannot start agents; other firms, walled users and non-members get 404.

## Approvals (AGT-03)

Agent actions join workflow steps in one queue (`GET /v1/approvals?mine=true`, the "to approve" badges). The approver sees the tool, its arguments and the agent's reasoning. **Approve**: the action runs. **Reject** (comment optional): the agent is told who rejected it and why, and carries on without it.

## Matter memory (AGT-05)

Per matter: facts, events (with dates), parties, deadlines and notes, each with the document, passage and page it came from, and whether a person or an agent saved it. Agents recall it (`recall_memory`) and add to it (`remember_fact`); people add and remove items in the console. Permissions follow the matter: everyone on it reads; editors add and remove (removal is a soft delete, audited).

## Subagents (AGT-06)

`delegate_to_subagents` starts one read-only subagent per document (up to 8), each limited to its document and given a share of the remaining budget. They run as separate jobs, so several workers process them in parallel. When the last one finishes it wakes the parent, whose next step sees every subagent's cited answer, with passage ids renumbered into the parent's own. Their costs count towards the parent's budget. Cancelling the parent cancels them.

## Trace (AGT-07)

`GET /v1/agent-runs/{id}` returns every step (thought, tool, arguments, observation, result, model, tokens, cost), approvals, subagents and the passages read. The audit chain records `agent.start`, every `agent.step`, every `ai.call`, approvals and decisions, and how the run ended.

## API

| Method & path | Purpose |
|---|---|
| GET /v1/agents · GET /v1/agents/{slug} · POST /v1/agents/validate · POST /v1/agents | definitions |
| POST /v1/matters/{id}/agent-runs · GET same | start / list (top-level runs) |
| GET /v1/agent-runs/{id} · POST …/cancel | the trace; cancel (with subagents) |
| GET /v1/approvals · POST /v1/approvals/{id}/decision | shared with workflows |
| GET /v1/matters/{id}/memory?q=&kind= · POST same · DELETE /v1/memory/{id} | matter memory |

## Console

**Agents** tab: choose an agent (its tools and which need approval are shown), give a goal, optionally limit documents and steps; follow the run as a numbered timeline of thoughts, tool calls with their arguments, what each found, approvals inline and subagents with their own traces; the cited answer opens each passage in the inspector. **Matter memory** lists, searches, adds and removes items.

## Try it

1. `bootstrap.bat`, then `console.bat`; sign in as **Rohan Gupta** (associate).
2. On *Share purchase agreement — Project Lotus*, upload an agreement and a notice, then Agents → *Chronology builder*, goal "Build a chronology of the dispute". It saves events to the memory and waits for you to confirm the draft.
3. Try *Reply to a legal notice*: the draft waits for **Asha Sharma** (partner, matter lead). Reject it with a comment and watch the agent adapt.

## Groq notes (found in the first live runs)

- gpt-oss sometimes produces JSON that Groq's strict validator rejects (HTTP 400 `json_validate_failed`). The gateway then retries in JSON mode with the schema in the prompt, and if that is rejected too, hands Groq's `failed_generation` to the caller, which validates it as usual. A reply that still cannot be read becomes an observation, so the agent corrects itself instead of the run failing.
- A one-value enum (only `final_answer` allowed) is enforced as `const` and was violated; the last step now uses its own answer-only schema.
- `scripts\windows\agent_report.bat` writes the latest runs, every step and any error to `logs\40_agents.log`.

## Deferred

| Item | Why / when |
|---|---|
| Agent Builder UI (AGT-08) | Phase 3; JSON definitions through the API now |
| Native provider tool calling, several tool calls per step | ADR-018; the decision format can change behind `run_agent_job` |
| Semantic search for agents | Step 1c |
| Editing an action's arguments before approving it | after feedback from lawyers |
