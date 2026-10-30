#!/usr/bin/env bash
# Dataviv AI OS - start Qdrant (if semantic search uses it), the API with its in-process worker, and
# open the test console. Stop with Ctrl+C (Qdrant keeps running: bash scripts/linux/qdrant.sh stop).
# Port 8010 by default; set DATAVIV_PORT to choose another. The next free port up to +20 is used.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
VPY="$root/.venv/bin/python"
[ -x "$VPY" ] || { echo "No .venv yet: run bash scripts/linux/setup.sh first."; exit 1; }
[ -f "$root/frontend/dist/index.html" ] || echo "Console not built yet (cd frontend && npm install && npm run build). Starting the API anyway; Swagger is at /docs."

if grep -Eq '^\s*DATAVIV_VECTOR_STORE\s*=\s*qdrant' "$root/backend/.env" 2>/dev/null; then
  bash "$root/scripts/linux/qdrant.sh"
fi

port="${DATAVIV_PORT:-8010}"
for p in $(seq "$port" $((port + 20))); do
  if ! (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null; then port=$p; break; fi
  echo "Port $p is in use - trying the next one."
done

if ! PGPASSWORD=app_user_dev_only psql -h localhost -U app_user -d dataviv -tAc 'SELECT 1' >/dev/null 2>&1; then
  echo "The dev database is not set up yet (cannot log in as app_user). Run: bash scripts/linux/setup_db.sh"
  exit 1
fi
echo "Refreshing seed data (idempotent)..."
"$VPY" "$root/scripts/seed_dev.py" >/dev/null
url="http://127.0.0.1:$port/ui/"
echo "$url" >"$root/logs/console_url.txt"
( for _ in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:$port/health" >/dev/null 2>&1; then xdg-open "$url" >/dev/null 2>&1 || true; break; fi
    sleep 1
  done ) &
echo "Console: $url   API docs: http://127.0.0.1:$port/docs   (Ctrl+C to stop)"
cd "$root/backend"
exec "$VPY" -m uvicorn app.main:app --port "$port"
