#!/usr/bin/env bash
# Dataviv AI OS - run the benchmark (Assistant answers + review-table extraction) and gate the release.
# Uses the TEST database, never your dev data. On Groq's developer tier this takes about 5-10 minutes
# (8K tokens-per-minute limit); progress is shown as it goes. Report: logs/evals/eval-*.md
#   bash scripts/linux/evals.sh                       all suites, model from backend/.env
#   bash scripts/linux/evals.sh --suite assistant --limit 5
#   bash scripts/linux/evals.sh --backend fake        offline smoke run, no model calls
set -uo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
VPY="$root/.venv/bin/python"
[ -x "$VPY" ] || { echo "No .venv yet: run bash scripts/linux/bootstrap.sh first."; exit 1; }
mkdir -p "$root/logs"
cd "$root/backend"
"$VPY" -m evals.run "$@" 2>&1 | tee "$root/logs/30_evals.log"
code=${PIPESTATUS[0]}
if [ "$code" -eq 0 ]; then
  printf '\n\033[32mBenchmark PASSED. Report in logs/evals/\033[0m\n'
else
  printf '\n\033[33mBenchmark FAILED a gate (or could not run). See logs/30_evals.log and logs/evals/\033[0m\n'
fi
exit "$code"
