#!/usr/bin/env bash
# Dataviv AI OS - prepare the NATIVE PostgreSQL 16 on Linux (Ubuntu/Debian). Run from the repo root:
#   bash scripts/linux/setup_db.sh
# Creates roles `dataviv` (schema owner) and `app_user` (what the API connects as, so RLS applies),
# databases `dataviv` and `dataviv_test`, applies migrations, and seeds dev data. Safe to re-run.
# Only the role/database step needs the postgres superuser: sudo asks for your password once,
# and the step is skipped entirely when the roles already exist.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
steps=4
n=0
step() { n=$((n + 1)); printf '\n\033[36m[%d/%d] %s\033[0m\n' "$n" "$steps" "$1"; }
VPY="$root/.venv/bin/python"
[ -x "$VPY" ] || { echo "No .venv found - run: bash scripts/linux/setup.sh"; exit 1; }

step "Checking for existing dev roles and databases"
if PGPASSWORD=dataviv_dev_only psql -h localhost -U dataviv -d dataviv_test -tAc 'SELECT 1' >/dev/null 2>&1; then
  echo "  roles and databases already exist - skipping the superuser step (no sudo needed)"
  n=$((n + 1))
else
  step "Creating roles and databases as the postgres superuser (sudo asks for your password once)"
  sudo -u postgres psql -v ON_ERROR_STOP=1 -q <<'SQL'
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dataviv') THEN
    CREATE ROLE dataviv LOGIN PASSWORD 'dataviv_dev_only';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') THEN
    CREATE ROLE app_user LOGIN PASSWORD 'app_user_dev_only';
  END IF;
END $$;
SELECT 'CREATE DATABASE dataviv OWNER dataviv'
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'dataviv')\gexec
SELECT 'CREATE DATABASE dataviv_test OWNER dataviv'
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'dataviv_test')\gexec
SQL
  echo "  roles dataviv and app_user, databases dataviv and dataviv_test are ready"
fi

step "Applying migrations to dataviv"
(cd backend && "$VPY" -m alembic upgrade head)

step "Seeding dev firm, users and matters"
"$VPY" scripts/seed_dev.py

printf '\n\033[32mDatabase ready. Start the API with: bash scripts/linux/console.sh  (or F5 in VS Code)\033[0m\n'
