#!/usr/bin/env bash
# Dataviv AI OS - one-time developer setup on Linux. Run from the repo root:  bash scripts/linux/setup.sh
# No sudo needed: uses uv (installed to ~/.local/bin if missing), so the distro's python3-venv
# package is not required. Creates .venv, installs backend + dev dependencies, creates backend/.env.
#   --semantic   also install CPU PyTorch + sentence-transformers for local bge-m3 embeddings (~1 GB)
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
semantic=false
[ "${1:-}" = "--semantic" ] && semantic=true
steps=5
$semantic && steps=6
n=0
step() { n=$((n + 1)); printf '\n\033[36m[%d/%d] %s\033[0m\n' "$n" "$steps" "$1"; }

step "Checking for uv"
UV="$(command -v uv || true)"
[ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"
if [ -z "$UV" ]; then
  echo "  installing uv to ~/.local/bin"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  UV="$HOME/.local/bin/uv"
fi
"$UV" --version

step "Creating virtual environment .venv (Python 3.12+)"
if [ -x .venv/bin/python ]; then
  echo "  .venv already exists - reusing it"
else
  if [ -d .venv ]; then
    echo "  .venv is not a Linux environment (copied from Windows?) - moving it to .venv-windows"
    mv .venv .venv-windows
  fi
  "$UV" venv .venv --python 3.12
fi

step "Installing backend and dev dependencies"
"$UV" pip install --python .venv/bin/python -e "./backend[dev]"

if $semantic; then
  step "Installing local embeddings: CPU PyTorch + sentence-transformers"
  "$UV" pip install --python .venv/bin/python --index-url https://download.pytorch.org/whl/cpu torch
  "$UV" pip install --python .venv/bin/python -e "./backend[local-embeddings]"
fi

step "Creating backend/.env from .env.example"
if [ -f backend/.env ]; then echo "  backend/.env already exists - left unchanged"; else cp backend/.env.example backend/.env; echo "  created backend/.env"; fi

step "Model provider"
# Switches the gateway to Groq when backend/.env holds a GROQ_API_KEY, unless it is set to a local model
# (DATAVIV_LLM_BACKEND=ollama), which is left alone. The key itself is never printed.
if grep -Eq '^\s*DATAVIV_LLM_BACKEND\s*=\s*ollama' backend/.env; then
  echo "  Using the local model server (DATAVIV_LLM_BACKEND=ollama)"
elif grep -Eq '^\s*(DATAVIV_)?GROQ_API_KEY\s*=\s*\S+' backend/.env; then
  if grep -Eq '^\s*DATAVIV_LLM_BACKEND\s*=\s*groq' backend/.env; then
    echo "  Using Groq (GROQ_API_KEY found, not shown)"
  elif grep -Eq '^\s*DATAVIV_LLM_BACKEND\s*=' backend/.env; then
    sed -i -E 's/^\s*DATAVIV_LLM_BACKEND\s*=.*/DATAVIV_LLM_BACKEND=groq/' backend/.env
    echo "  GROQ_API_KEY found (not shown): set DATAVIV_LLM_BACKEND=groq"
  else
    echo "DATAVIV_LLM_BACKEND=groq" >>backend/.env
    echo "  GROQ_API_KEY found (not shown): set DATAVIV_LLM_BACKEND=groq"
  fi
else
  echo "  No GROQ_API_KEY in backend/.env - the Assistant uses the offline test model. Add GROQ_API_KEY=... by hand."
fi

printf '\n\033[32mSetup done. Next: bash scripts/linux/setup_db.sh\033[0m\n'
