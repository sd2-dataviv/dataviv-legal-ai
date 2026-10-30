#!/usr/bin/env bash
# Makes one tiny call to the model provider (Groq) to check the key and model. Never prints the key.
set -uo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
mkdir -p logs
"$root/.venv/bin/python" scripts/check_llm.py 2>&1 | tee logs/20_check_llm.log
exit "${PIPESTATUS[0]}"
