# Architecture Decision Records

| ADR | Decision | Status |
|---|---|---|
| 001 | Modular monolith (one API, one worker image) before microservices | Accepted |
| 002 | PostgreSQL row-level security for tenancy, plus app-level filters | Accepted |
| 003 | Qdrant for vectors (dense + sparse) with mandatory ACL payload filters | Accepted |
| 004 | Single `accessible_matter_ids()` permission function; OpenFGA later behind it | Accepted |
| 005 | Temporal for durable jobs, workflows and agent runs | Accepted |
| 006 | LiteLLM gateway with task classes; FakeLLM in tests | Accepted |
| 007 | Inaccessible matters return 404, not 403 | Accepted |

## ADR-002 — PostgreSQL RLS for tenancy

**Context.** A single missed `WHERE firm_id = …` in application code would leak one firm's data to another.
**Decision.** Enable RLS on every tenant table; the API sets `app.firm_id` per transaction and connects as a non-owner role. Application queries still filter by `firm_id`.
**Consequences.** Leaks need two independent bugs. Migrations must add a policy to every new tenant table (enforced by a test that inspects `pg_class.relrowsecurity`).

## ADR-007 — 404 for inaccessible matters

**Context.** A 403 confirms a matter exists, which can itself reveal a confidential client relationship.
**Decision.** Any matter-scoped object the principal cannot access returns 404.
**Consequences.** Slightly harder debugging; audit log records the denied attempt.
