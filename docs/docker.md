# Running Dataviv AI OS in Docker

One image (`dataviv-ai-os`) runs the API, the background worker and the test console. `docker-compose.yml`
at the repo root adds PostgreSQL 16 and Qdrant.

```
            :8080                       (internal network "dataviv_default")
browser ──────────► api  ─────────────► postgres:5432   app_user (RLS) for requests, dataviv for migrations
  /ui, /docs,       │  in-process        qdrant:6333     vectors (semantic search)
  /v1, WebSocket    │  worker            Groq (internet) model calls
                    └── volume appdata:/app/var   document originals + bge-m3 model cache
```

## Everyday use

| Command | What it does |
|---|---|
| `bash scripts/linux/docker.sh up` | Build the image and start the stack; waits until the api is healthy |
| `bash scripts/linux/docker.sh status` | Containers, health, CPU and memory |
| `bash scripts/linux/docker.sh logs` | Follow the api log |
| `bash scripts/linux/docker.sh smoke` | End-to-end check (uploads a sample SPA, makes one Groq call, creates a draft) |
| `bash scripts/linux/docker.sh down` | Stop; data stays in the volumes |
| `bash scripts/linux/docker.sh reset` | Stop and delete all container data (asks first) |

Plain Compose works too: `docker compose up -d --build`, `docker compose logs -f api`, `docker compose down`.

Inside the container, the entrypoint takes these commands:
`docker compose exec api dataviv seed` (prints dev tokens), `... dataviv migrate`, `... dataviv reindex`,
`... dataviv check-llm`, `... dataviv shell`.

## Configuration

- **`backend/.env` is read at run time** (`env_file`), so the Groq key, `DATAVIV_LLM_BACKEND`, and the
  embeddings settings are the same as for the native setup. The database, Qdrant and storage addresses
  in that file are overridden for the containers. `.dockerignore` keeps `.env` out of the image.
- Variables read by Compose (shell, or a `.env` file next to `docker-compose.yml`):

| Variable | Default | Meaning |
|---|---|---|
| `DATAVIV_HTTP_PORT` | `8080` | Host port for the console and API (8010 stays free for the native setup) |
| `DATAVIV_SEMANTIC` | `1` | `0` builds a slim image without PyTorch (keyword search, or embeddings over HTTP/TEI) |
| `DATAVIV_SEED_ON_START` | `true` | Refresh the dev firm and users on start (ignored outside dev/test) |
| `DATAVIV_RUN_WORKER_IN_API` | `true` | `false` + `--profile worker` runs jobs in a separate `worker` container |
| `POSTGRES_PASSWORD`, `APP_DB_PASSWORD` | dev values | Database passwords (`APP_DB_PASSWORD` read by `docker/postgres/00-init.sh` when the volume is first created) |

Separate worker: `DATAVIV_RUN_WORKER_IN_API=false docker compose --profile worker up -d`. Each process
that embeds loads bge-m3 (about 1.5 GB of RAM), so this needs more memory than the default.

## The image

- Multi-stage: Node 20 builds the console; `uv` installs Python 3.12 dependencies into `/opt/venv`
  (CPU PyTorch + sentence-transformers when `SEMANTIC=1`); the runtime runs as user `dataviv` (uid 10001)
  under `tini`. About 2.1 GB with PyTorch.
- On start, `api` waits for Postgres, applies migrations (`alembic upgrade head`), seeds dev data, and
  serves on port 8010 inside the container. A `HEALTHCHECK` polls `/health`.
- The first upload or semantic search downloads bge-m3 (about 2.2 GB) into the `appdata` volume, once.
- Postgres and Qdrant are not published to the host. To reach Postgres, uncomment its `ports` line
  (`127.0.0.1:5433:5432`).

## Notes

- **Docker Desktop memory.** Docker Desktop gives its VM about 3.7 GB by default. The default stack uses
  about 1.7 GB once bge-m3 is loaded. If you run the separate worker, or larger documents, raise it in Docker
  Desktop → Settings → Resources.
- **Data is separate from the native setup.** The containers use their own database and storage volumes,
  and start empty apart from the seeded firm. To bring native data across, dump it with `pg_dump` and
  restore it into the `postgres` container. Then copy `var/storage` into the `appdata` volume and run
  `dataviv reindex`.
- **Secrets.** Values from `env_file` are visible to anyone who can run `docker inspect` on this machine.
  For a shared server, use Docker secrets or the orchestrator's secret store instead of `backend/.env`.
- **Production.** Set `DATAVIV_ENV=prod` (turns off dev tokens and seeding), a real `DATAVIV_JWT_SECRET`
  and database passwords, and put TLS in front (nginx/Caddy). That proxy must pass WebSocket upgrades
  for `/v1/drafts/{id}/live`. Set `DATAVIV_FORWARDED_ALLOW_IPS` to the proxy's address.
- Verified on Ubuntu with Docker Desktop 29: `scripts/docker_smoke.py` passes. It checks upload, ingest
  with bge-m3, keyword and semantic search through Qdrant, two users co-editing over the WebSocket, a Groq
  suggestion, accept, save, the approval lock (close code 4423) and approval. The separate-worker profile
  and data persistence across restarts are also verified.

## E-signature (DocuSign) in Docker

`backend/.env` names the DocuSign private key by its path on this machine (`DATAVIV_DOCUSIGN_PRIVATE_KEY_PATH`),
which the container cannot see. `scripts/linux/docker.sh` reads that file when it starts the stack and passes the
key text in as `DATAVIV_DOCUSIGN_PRIVATE_KEY`, an environment value only: it is never written into the image,
the compose file or a volume. Without a readable key, e-signature shows as "not configured" and the rest works.

## "No containers" after opening Docker Desktop

Opening Docker Desktop switches the Docker command line to its own engine (`docker context ls` shows
`desktop-linux *`). The stack started with the regular engine keeps running there; to see or manage it, switch
back with `docker context use default`, or prefix a command with `DOCKER_CONTEXT=default`.

## Workflow Platform zip

`scripts/platform_zip.py` builds `~/Desktop/dataviv_ai_os-workflow-app.zip` (application: `platform/docker-compose.yml`
and `platform/node.yaml` at the top). By default it holds no secrets. `--with-env` carries `backend/.env` into it
on purpose, for platforms with nowhere to enter environment values: as `.env` at the top and written into
`docker-compose.yml`, leaving out what only fits this machine (localhost database URLs, GPU, storage) and swapping
the DocuSign key file for its text. That zip holds the Groq key, JWT secret and DocuSign key: share it like a password.

### Platform builds use the legacy builder

WorkflowHub builds with Docker's legacy builder (no BuildKit). The platform compose file therefore builds only
one image (`web`) and uses the stock `postgres:16-alpine` for the database; the web container creates the
`app_user` role itself on first start (`DATAVIV_BOOTSTRAP_APP_ROLE=true`, password from `DATAVIV_DATABASE_URL`).
The local `docker-compose.yml` still builds `docker/postgres`, which does the same job with an init script.
To reproduce a platform build: `DOCKER_BUILDKIT=0 docker build -t test .` from an unzipped copy.
