#!/usr/bin/env bash
# Dataviv AI OS - install and build the React test console (frontend/). Logs go to logs/.
# After a successful build FastAPI serves the console at /ui (bash scripts/linux/console.sh starts it).
set -uo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
mkdir -p logs
status="$root/logs/frontend_status.txt"
echo "started $(date -Is)" >"$status"
mark() { echo "$1 $(date -Is)" >>"$status"; printf '\n\033[33m=== %s ===\033[0m\n' "$1"; }

if ! command -v npm >/dev/null; then
  mark "npm not found: install Node.js 20+ (e.g. from nodesource or nvm), then run this again"
  exit 1
fi
mark "node $(node -v), npm $(npm -v)"
major=$(node -v | sed 's/^v\([0-9]*\).*/\1/')
[ "$major" -lt 20 ] && echo "  note: Node $major works with this console (Vite 6) but Node 20+ is recommended"

cd frontend
mark "STEP 1/3 npm install (first run downloads about 40 MB)"
npm install --no-audit --no-fund 2>&1 | tee ../logs/10_npm_install.log
mark "npm install exit=${PIPESTATUS[0]}"
# node_modules copied from Windows lose their executable bits
chmod +x node_modules/.bin/* 2>/dev/null || true

mark "STEP 2/3 type check"
npm run typecheck 2>&1 | tee ../logs/11_typecheck.log
mark "typecheck exit=${PIPESTATUS[0]}"

mark "STEP 3/3 build"
npm run build 2>&1 | tee ../logs/12_build.log
build=${PIPESTATUS[0]}
mark "build exit=$build"
cd "$root"
if [ "$build" -eq 0 ] && [ -f frontend/dist/index.html ]; then
  mark "FINISHED: console built. Run bash scripts/linux/console.sh, it opens the console (port 8010 by default)"
else
  mark "FINISHED WITH ERRORS: see logs/10_npm_install.log, 11_typecheck.log and 12_build.log"
  exit 1
fi
