## What and why

<!-- One or two sentences. Link the requirement IDs this implements, e.g. VLT-03, AST-04. -->

Requirements: 

## Checklist

- [ ] Requirement IDs listed above and referenced in test docstrings
- [ ] New tenant tables carry `firm_id`, enable RLS, add `tenant_isolation` policy, grant `app_user`
- [ ] Matter-scoped access goes through `app.core.permissions` (no ad-hoc membership queries)
- [ ] Inaccessible objects return 404, not 403 (ADR-007)
- [ ] Every create/update/delete and AI call writes an audit event
- [ ] Long-running work updates a `jobs` row (progress shown to users)
- [ ] Model calls go through `app.core.llm` with a task class
- [ ] Tests added; `make check` passes locally
- [ ] Docs updated (`docs/02-design.md`, ADR if a decision changed)
