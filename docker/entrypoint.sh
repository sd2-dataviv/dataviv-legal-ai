#!/bin/sh
# Dataviv AI OS container entrypoint.
#   api        (default) wait for the database (answering with a 'starting' page meanwhile), migrate,
#              seed (dev only), then serve on $DATAVIV_PORT
#   worker     run the background job worker on its own (set DATAVIV_RUN_WORKER_IN_API=false on the api)
#   migrate    apply database migrations and exit
#   seed       load the dev firm, users and matters and print dev tokens
#   reindex    rebuild the vector index from the chunks in Postgres
#   check-llm  one test call to the configured model provider
#   shell      a shell inside the container; anything else is run as a command
set -eu
cd /app/backend

log() { printf '[dataviv] %s\n' "$*"; }

db_ready() {
  python - <<'PY2' >/dev/null 2>&1
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.config import get_settings

async def main() -> None:
    engine = create_async_engine(get_settings().database_owner_url, connect_args={"timeout": 5})
    async with engine.connect():
        pass
    await engine.dispose()

asyncio.run(main())
PY2
}

# wait_for_db [seconds]: 0 = keep waiting (the api answers via starting.py meanwhile)
wait_for_db() {
  tries="${1:-${DATAVIV_DB_WAIT_SECONDS:-60}}"
  i=0
  until db_ready; do
    i=$((i + 1))
    if [ "$tries" -gt 0 ] && [ "$i" -ge "$tries" ]; then
      log "database not reachable after ${tries}s - check DATAVIV_DATABASE_OWNER_URL"
      exit 1
    fi
    if [ "$i" -le 30 ]; then [ $((i % 5)) -eq 1 ] && log "waiting for the database... (${i}s)"
    else [ $((i % 60)) -eq 0 ] && log "still waiting for the database (${i}s) - is the postgres service running?"
    fi
    sleep 1
  done
  log "database is up"
}

# DATAVIV_BOOTSTRAP_APP_ROLE=true (the hosting-platform compose file): create the app_user role that
# DATAVIV_DATABASE_URL names, with that URL's password, if it does not exist yet. This lets the stack use the
# stock postgres image (nothing to build); docker/postgres does the same job for the local compose file.
bootstrap_app_role() {
  [ "${DATAVIV_BOOTSTRAP_APP_ROLE:-false}" = "true" ] || return 0
  python - <<'PY'
import asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.config import get_settings

async def main() -> None:
    s = get_settings()
    app = make_url(s.database_url)
    engine = create_async_engine(s.database_owner_url)
    async with engine.begin() as conn:
        exists = await conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :u"), {"u": app.username})
        if exists:
            print(f"[dataviv] database role {app.username} exists", flush=True)
        else:
            sql = await conn.scalar(
                text("SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', CAST(:u AS text), CAST(:p AS text))"),
                {"u": app.username, "p": app.password or ""},
            )
            await conn.execute(text(sql))
            print(f"[dataviv] created database role {app.username}", flush=True)
    await engine.dispose()

asyncio.run(main())
PY
}

migrate() {
  bootstrap_app_role
  log "applying migrations"
  python -m alembic upgrade head
}

seed() {
  log "seeding dev firm, users and matters"
  python /app/scripts/seed_dev.py "$@"
}

cmd="${1:-api}"
[ $# -gt 0 ] && shift

case "$cmd" in
  api)
    # Answer on the port at once (a placeholder page and /health), so platforms and load balancers that
    # probe straight away see a live container; the real app takes the port once the database is ready.
    placeholder=""
    if ! db_ready; then
      python /app/scripts/starting.py &
      placeholder=$!
      log "database not ready yet: answering on :${DATAVIV_PORT:-8010} with a 'starting' page meanwhile"
    fi
    wait_for_db "${DATAVIV_DB_WAIT_SECONDS:-0}"
    [ "${DATAVIV_MIGRATE_ON_START:-true}" = "true" ] && migrate
    if [ "${DATAVIV_SEED_ON_START:-false}" = "true" ]; then
      case "${DATAVIV_ENV:-dev}" in
        dev|test) seed >/dev/null && log "seed data refreshed" ;;
        *) log "DATAVIV_SEED_ON_START ignored outside dev/test" ;;
      esac
    fi
    if [ -n "$placeholder" ]; then
      kill "$placeholder" 2>/dev/null || true
      wait "$placeholder" 2>/dev/null || true
    fi
    log "serving on 0.0.0.0:${DATAVIV_PORT:-8010} (console at /ui, API docs at /docs)"
    exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${DATAVIV_PORT:-8010}" \
      --proxy-headers --forwarded-allow-ips="${DATAVIV_FORWARDED_ALLOW_IPS:-127.0.0.1}" "$@"
    ;;
  worker)
    wait_for_db
    log "starting the background worker"
    exec python -m app.worker "$@"
    ;;
  migrate) wait_for_db; migrate ;;
  seed) wait_for_db; seed "$@" ;;
  reindex) wait_for_db; exec python /app/scripts/reindex_vectors.py "$@" ;;
  check-llm) exec python /app/scripts/check_llm.py "$@" ;;
  shell) exec /bin/bash "$@" ;;
  *) exec "$cmd" "$@" ;;
esac
