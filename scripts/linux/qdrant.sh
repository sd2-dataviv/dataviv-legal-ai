#!/usr/bin/env bash
# Dataviv AI OS - run Qdrant (the vector store for semantic search) without Docker.
#   bash scripts/linux/qdrant.sh          start in the background (downloads the binary on first run)
#   bash scripts/linux/qdrant.sh stop     stop it
#   bash scripts/linux/qdrant.sh status   is it up?
# Data lives in var/qdrant/storage; logs in logs/qdrant.log. Listens on 127.0.0.1:6333 only.
set -euo pipefail
VERSION="v1.19.2"
root="$(cd "$(dirname "$0")/../.." && pwd)"
dir="$root/var/qdrant"
bin="$dir/qdrant"
pidfile="$dir/qdrant.pid"
mkdir -p "$dir" "$root/logs"

up() { curl -fsS http://127.0.0.1:6333/readyz >/dev/null 2>&1; }

case "${1:-start}" in
  stop)
    if [ -f "$pidfile" ] && kill "$(cat "$pidfile")" 2>/dev/null; then echo "Qdrant stopped"; else echo "Qdrant was not running"; fi
    rm -f "$pidfile"
    exit 0 ;;
  status)
    if up; then echo "Qdrant is up on http://127.0.0.1:6333 (dashboard: /dashboard)"; else echo "Qdrant is not running"; exit 1; fi
    exit 0 ;;
esac

if up; then echo "Qdrant already running on http://127.0.0.1:6333"; exit 0; fi
if [ ! -x "$bin" ]; then
  echo "Downloading Qdrant $VERSION (about 30 MB)..."
  curl -L --progress-bar -o "$dir/qdrant.tar.gz" \
    "https://github.com/qdrant/qdrant/releases/download/$VERSION/qdrant-x86_64-unknown-linux-gnu.tar.gz"
  tar -xzf "$dir/qdrant.tar.gz" -C "$dir" && rm "$dir/qdrant.tar.gz"
fi
cd "$dir"
QDRANT__SERVICE__HOST=127.0.0.1 QDRANT__STORAGE__STORAGE_PATH="$dir/storage" \
QDRANT__STORAGE__SNAPSHOTS_PATH="$dir/snapshots" QDRANT__TELEMETRY_DISABLED=true \
  nohup "$bin" >>"$root/logs/qdrant.log" 2>&1 &
echo $! >"$pidfile"
printf 'Starting Qdrant'
for _ in $(seq 1 30); do
  if up; then printf '\nQdrant is up on http://127.0.0.1:6333 (dashboard: /dashboard)\n'; exit 0; fi
  printf '.'; sleep 1
done
printf '\nQdrant did not start - see logs/qdrant.log\n'
exit 1
