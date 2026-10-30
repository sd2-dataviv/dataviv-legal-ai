# Dataviv AI OS test console

React 18 + Vite + TypeScript. A console for exercising the backend by hand, not the product UI.

| Area | What it calls |
|---|---|
| Identity switcher | `GET /v1/dev/identities` (dev only): every seeded user with a fresh token |
| Health dot | `GET /health` every 15 s |
| Matters rail | `GET/POST /v1/matters` (CNR checked client-side but still sent, so you can see the 422) |
| Documents tab | `GET /v1/matters/{id}/documents` with type/status filters; polls while anything is processing |
| Upload tab | `POST /v1/matters/{id}/documents`, then `GET /v1/jobs/{id}` every second for the progress bar |
| People tab | `GET /v1/matters/{id}/people`, `GET /v1/users`, `POST …/members`, `POST …/walls` |
| Search tab | `POST /v1/search`; « » markers become highlighted text |
| Inspector | `GET /v1/documents/{id}`, `…/download`, `DELETE …`, `GET …/chunks/{chunk_id}` |
| Request log | Every call: method, path, status, milliseconds, acting user, and the problem+json body on failure |

The console never hides an action because of your role. Whether it is allowed is the API's decision, and a 403 or 404
is a result to check.

```bash
npm install
npm run dev        # http://localhost:5173, proxies /v1 and /health to the API on :8010 (set VITE_API_PORT to change)
npm run build      # dist/, served by FastAPI at http://127.0.0.1:8010/ui
```
