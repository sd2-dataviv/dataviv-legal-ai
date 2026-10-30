#!/usr/bin/env bash
# Writes the latest agent runs, with every step and error, to logs/40_agents.log (optional arg: how many runs).
set -uo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
mkdir -p logs
"$root/.venv/bin/python" scripts/agent_report.py "$@" >logs/40_agents.log 2>&1
cat logs/40_agents.log
echo
echo "Saved to logs/40_agents.log"
