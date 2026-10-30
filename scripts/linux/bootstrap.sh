#!/usr/bin/env bash
# Dataviv AI OS - full first run on Linux: setup -> database -> format/lint/types -> tests -> model check.
# Keeps going when a step fails; every step's output is in logs/, and logs/bootstrap_status.txt is the summary.
#   bash scripts/linux/bootstrap.sh              (add --semantic to also install local bge-m3 embeddings)
# The database step asks for your sudo password once, the first time only.
set -uo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
mkdir -p logs
status="$root/logs/bootstrap_status.txt"
echo "started $(date -Is)" >"$status"
mark() { echo "$1 $(date -Is)" >>"$status"; printf '\n\033[33m=== %s ===\033[0m\n' "$1"; }
VPY="$root/.venv/bin/python"

mark "STEP 1/5 setup"
if bash scripts/linux/setup.sh "$@" 2>&1 | tee logs/01_setup.log; then mark "setup OK"; else mark "setup FAILED (see logs/01_setup.log)"; fi

mark "STEP 2/5 database"
db_ok=true
if bash scripts/linux/setup_db.sh 2>&1 | tee logs/02_database.log && [ "${PIPESTATUS[0]}" -eq 0 ]; then
  mark "database OK"
else
  db_ok=false
  mark "database FAILED (see logs/02_database.log) - tests will be skipped; fix this and run again"
fi

cd backend
mark "STEP 3/5 format, lint + types"
"$VPY" -m ruff format . 2>&1 | tee ../logs/03_format_applied.log
"$VPY" -m ruff check --fix . 2>&1 | tee ../logs/03_fix_applied.log
"$VPY" -m ruff check . 2>&1 | tee ../logs/03_ruff.log; mark "ruff exit=${PIPESTATUS[0]}"
"$VPY" -m ruff format --check . 2>&1 | tee ../logs/03_ruff_format.log; mark "ruff-format exit=${PIPESTATUS[0]}"
"$VPY" -m mypy app 2>&1 | tee ../logs/03_mypy.log; code=${PIPESTATUS[0]}
if [ "$code" -eq 2 ]; then  # mypy itself crashed (often a stale cache): clear it and retry with a traceback
  mark "mypy crashed (exit 2): clearing .mypy_cache and retrying"
  rm -rf .mypy_cache ../.mypy_cache
  "$VPY" -m mypy app --show-traceback 2>&1 | tee ../logs/03_mypy.log; code=${PIPESTATUS[0]}
fi
mark "mypy exit=$code"

mark "STEP 4/5 tests"
if $db_ok; then
  "$VPY" -m pytest -rA --tb=short --color=no 2>&1 | tee ../logs/04_pytest.log; mark "pytest exit=${PIPESTATUS[0]}"
else
  mark "tests SKIPPED: the database is not set up (step 2 failed)"
fi
cd "$root"

mark "STEP 5/5 model provider check (one tiny call)"
bash scripts/linux/check_llm.sh 2>&1 | tee logs/05_check_llm.log; mark "llm-check exit=${PIPESTATUS[0]}"
mark "FINISHED"
printf '\n\033[32mAll done. Summary: logs/bootstrap_status.txt\033[0m\n'
