#!/usr/bin/env bash
# Dataviv AI OS in Docker. Run from anywhere:
#   bash scripts/linux/docker.sh up        build the image and start postgres, qdrant and the api
#   bash scripts/linux/docker.sh status    containers, health and memory
#   bash scripts/linux/docker.sh logs      follow the api log (Ctrl+C to stop following)
#   bash scripts/linux/docker.sh smoke     end-to-end check (uploads a sample, one Groq call, live drafts)
#   bash scripts/linux/docker.sh down      stop the stack (data stays in the volumes)
#   bash scripts/linux/docker.sh reset     stop and DELETE all container data (asks first)
# Options via environment: DATAVIV_HTTP_PORT (default 8080), DATAVIV_SEMANTIC=0 (slim image, no PyTorch).
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
port="${DATAVIV_HTTP_PORT:-8080}"
c() { printf '\033[36m%s\033[0m\n' "$*"; }

if ! docker info >/dev/null 2>&1; then
  echo "Cannot reach Docker. Start Docker Desktop, or add yourself to the docker group:"
  echo "  sudo usermod -aG docker \"\$USER\"   (then log out and back in)"
  exit 1
fi
[ -f backend/.env ] || echo "Note: backend/.env not found - the stack runs with defaults (fake model, keyword search)."

# DocuSign: backend/.env names a key FILE on this machine, which the container cannot see. Hand the key text in
# as an environment value instead (never written into the image or any file).
if [ -z "${DATAVIV_DOCUSIGN_PRIVATE_KEY:-}" ] && [ -f backend/.env ]; then
  keyfile="$(grep -E '^DATAVIV_DOCUSIGN_PRIVATE_KEY_PATH=' backend/.env | tail -1 | cut -d= -f2- | tr -d '\r"'"'"'')"
  if [ -n "$keyfile" ] && [ -r "$keyfile" ]; then
    DATAVIV_DOCUSIGN_PRIVATE_KEY="$(cat "$keyfile")"
    export DATAVIV_DOCUSIGN_PRIVATE_KEY
  elif [ -n "$keyfile" ]; then
    echo "Note: DocuSign key file in backend/.env is not readable - e-signature will show as not configured."
  fi
fi

wait_healthy() {
  local s=""
  for i in $(seq 1 90); do
    s="$(docker inspect -f '{{.State.Health.Status}}' dataviv-api-1 2>/dev/null || echo missing)"
    printf '\r  api: %-10s %3ss' "$s" "$((i * 2))"
    [ "$s" = healthy ] && { echo; return 0; }
    [ "$s" = unhealthy ] && break
    sleep 2
  done
  echo; echo "The api did not become healthy. Last log lines:"; docker compose logs --tail 30 api; return 1
}

case "${1:-up}" in
  up)
    c "[1/3] Building the image (first build ~2 min; later builds reuse cached layers)"
    docker compose build api
    c "[2/3] Starting postgres, qdrant and the api"
    docker compose up -d
    c "[3/3] Waiting for the api (migrations + seed run on start)"
    wait_healthy
    printf '\n\033[32mConsole: http://localhost:%s/ui/   API docs: http://localhost:%s/docs\033[0m\n' "$port" "$port"
    ;;
  status)
    docker compose ps
    docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' $(docker compose ps -q)
    ;;
  logs) docker compose logs -f --tail 100 api ;;
  smoke)
    py="$root/.venv/bin/python"
    [ -x "$py" ] || py=python3
    DATAVIV_BASE="127.0.0.1:$port" "$py" scripts/docker_smoke.py
    ;;
  down) docker compose --profile worker down ;;
  reset)
    read -r -p "Delete the containers AND all their data (database, documents, vectors, model cache)? [y/N] " ok
    [ "$ok" = y ] || { echo "Cancelled."; exit 0; }
    docker compose --profile worker down -v
    ;;
  *) sed -n '2,10p' "$0"; exit 1 ;;
esac
