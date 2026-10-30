#!/usr/bin/env bash
# Restart the local API (port 8010) so it re-reads backend/.env, then show whether e-signature works.
# Prints only the provider, account name and any fix needed - never keys or IDs.
#   bash scripts/linux/esign_check.sh
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
c() { printf '\033[36m%s\033[0m\n' "$*"; }

c "[1/3] Checking backend/.env"
for k in DATAVIV_ESIGN_BACKEND DATAVIV_DOCUSIGN_INTEGRATION_KEY DATAVIV_DOCUSIGN_USER_ID DATAVIV_DOCUSIGN_PRIVATE_KEY_PATH; do
  if grep -Eq "^\s*${k}\s*=\s*\S" backend/.env; then echo "  $k is set"; else echo "  $k is NOT set"; fi
done
keyfile="$(grep -E '^\s*DATAVIV_DOCUSIGN_PRIVATE_KEY_PATH\s*=' backend/.env | tail -1 | cut -d= -f2- | tr -d '\r' | xargs || true)"
keyfile="${keyfile/#\~/$HOME}"
if [ -n "$keyfile" ]; then
  if [ -r "$keyfile" ] && grep -q "BEGIN RSA PRIVATE KEY\|BEGIN PRIVATE KEY" "$keyfile"; then
    echo "  private key file found and looks like a PEM key"
  else
    echo "  private key file missing or not a PEM key: $keyfile"
  fi
fi

c "[2/3] Restarting the API on port 8010"
for pid in $(ps -eo pid,args | grep "[u]vicorn app.main:app --port 8010" | awk '{print $1}'); do kill "$pid"; done
sleep 1
(cd backend && nohup ../.venv/bin/python -m uvicorn app.main:app --port 8010 > ../logs/24_api_live.log 2>&1 &)
for i in $(seq 1 30); do
  curl -fsS http://127.0.0.1:8010/health >/dev/null 2>&1 && break
  printf '\r  waiting for the API... %ss' "$i"; sleep 1
done
echo

c "[3/3] E-signature status"
.venv/bin/python - <<'PY'
import httpx
c = httpx.Client(base_url="http://127.0.0.1:8010", timeout=60)
token = c.get("/v1/dev/identities").json()[0]["access_token"]
s = c.get("/v1/esign/status", headers={"Authorization": f"Bearer {token}"}).json()
print(f"  provider: {s['backend']}   ready: {s['ready']}")
if s.get("environment"):
    print(f"  environment: {s['environment']}   account: {s['account_name']}")
if s.get("detail"):
    print(f"  to fix: {s['detail']}")
if s.get("consent_url"):
    print("  open this link once, sign in as the DocuSign user and click Accept:")
    print(f"  {s['consent_url']}")
PY
