"""Build the Workflow Platform upload zip for Dataviv AI OS (an APPLICATION: docker-compose.yml + node.yaml).

    .venv/bin/python scripts/platform_zip.py [--out ~/Desktop/dataviv_ai_os-workflow-app.zip] [--with-env]

Only an allowlist of project files goes in. Secrets (backend/.env, keys), local data (var/, logs/),
virtual environments, node_modules and caches never do; the zip is scanned for secrets before it is
written, and the build stops if anything looks like one.

--with-env (asked for when the platform has nowhere to enter environment values) carries backend/.env
into the zip ON PURPOSE: as .env at the top, and its values written into docker-compose.yml so the platform
uses them however it runs the file. Lines that only make sense on this machine are left out (the localhost
database URLs, which hold the local Postgres password; storage, port and GPU settings), and the DocuSign key
FILE named in .env is replaced by the key text. The zip then holds secrets: share it like a password.
Values are never printed, only setting names.
"""

import argparse
import fnmatch
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INCLUDE = ["Dockerfile", ".dockerignore", "README.md", "Makefile", "docker", "backend", "frontend", "scripts", "docs"]
EXCLUDE = [
    "*/__pycache__/*", "*.pyc", "*/.mypy_cache/*", "*/.ruff_cache/*", "*/.pytest_cache/*", "*.egg-info/*",
    "backend/.env", "backend/.env.*", "*.key", "*.pem", "*.p12",
    "frontend/node_modules/*", "frontend/dist/*", "frontend/tsconfig.tsbuildinfo",
    "scripts/windows/*", "*.bat", "*.ps1", ".coverage", "htmlcov/*",
]
KEEP = ["backend/.env.example"]
# Platform files replace the local ones at the top of the zip
PLACED = {"docker-compose.yml": "platform/docker-compose.yml", "node.yaml": "platform/node.yaml"}
SECRETS = [
    (re.compile(rb"gsk_[A-Za-z0-9]{20,}"), "a Groq API key"),
    (re.compile(rb"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"), "a private key"),
    (re.compile(rb"(?m)^\s*GROQ_API_KEY\s*=\s*\S{8,}"), "a GROQ_API_KEY value"),
]


def wanted(rel: str) -> bool:
    if rel in KEEP:
        return True
    return not any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch("/" + rel, "*/" + p.lstrip("*/")) for p in EXCLUDE)


def files() -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for name in INCLUDE:
        p = ROOT / name
        if p.is_file():
            out.append((name, p))
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                rel = f.relative_to(ROOT).as_posix()
                if f.is_file() and wanted(rel):
                    out.append((rel, f))
    out += [(arc, ROOT / src) for arc, src in PLACED.items()]
    return out


# --with-env: settings from backend/.env that describe THIS machine, not the container
LOCAL_ONLY = {
    "DATAVIV_DATABASE_URL", "DATAVIV_DATABASE_OWNER_URL",  # localhost + the local Postgres password
    "DATAVIV_DOCUSIGN_PRIVATE_KEY_PATH",  # replaced by DATAVIV_DOCUSIGN_PRIVATE_KEY (the text)
    "DATAVIV_STORAGE_DIR", "DATAVIV_PORT", "DATAVIV_QDRANT_URL", "DATAVIV_EMBEDDINGS_DEVICE",
    "DATAVIV_FORWARDED_ALLOW_IPS", "DATAVIV_HOST",
}


def read_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip().removeprefix("export ").strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        env[k.strip()] = v
    return env


def platform_env() -> tuple[dict[str, str], list[str]]:
    """backend/.env made fit for the container. Returns (values, notes about what changed, names only)."""
    src = ROOT / "backend" / ".env"
    if not src.is_file():
        sys.exit("--with-env: backend/.env not found")
    env, notes = read_env(src), []
    keyfile = env.get("DATAVIV_DOCUSIGN_PRIVATE_KEY_PATH", "")
    if keyfile and not env.get("DATAVIV_DOCUSIGN_PRIVATE_KEY"):
        kp = Path(keyfile).expanduser()
        if kp.is_file():
            env["DATAVIV_DOCUSIGN_PRIVATE_KEY"] = kp.read_text(encoding="utf-8").strip().replace("\n", "\\n")
            notes.append("DocuSign key: file contents carried as DATAVIV_DOCUSIGN_PRIVATE_KEY")
        else:
            notes.append("DocuSign key file named in .env not found: e-signature will show as not configured")
    hook = env.get("DATAVIV_DOCUSIGN_WEBHOOK_URL", "")
    if hook and (not hook.startswith("https://") or any(h in hook for h in ("localhost", "127.0.0.1"))):
        env.pop("DATAVIV_DOCUSIGN_WEBHOOK_URL")
        notes.append("DocuSign webhook URL points at this machine: dropped (status is polled instead)")
    dropped = sorted(k for k in env if k in LOCAL_ONLY)
    for k in dropped:
        env.pop(k)
    if dropped:
        notes.append("left out (this machine only): " + ", ".join(dropped))
    return {k: v for k, v in env.items() if v != ""}, notes


def yaml_value(v: str) -> str:
    return json.dumps(v.replace("$", "$$"))  # JSON strings are valid YAML; $$ = a literal $ for compose


def render_compose(text: str, env: dict[str, str]) -> tuple[str, list[str]]:
    """Write .env values into the web service's environment (the platform may not read a .env file)."""
    used: set[str] = set()

    def sub(m: re.Match[str]) -> str:
        name = m.group(3)
        if name in env:
            used.add(name)
            return f"{m.group(1)}{m.group(2)}: {yaml_value(env[name])}"
        return m.group(0)

    text = re.sub(r"(?m)^(\s+)([A-Z0-9_]+): \$\{([A-Z0-9_]+)(?::-[^}]*)?\}[ \t]*(#.*)?$", sub, text)
    # settings the compose file does not list yet go at the end of web's environment block
    present = set(re.findall(r"(?m)^\s+([A-Z0-9_]+):", text))
    extra = [k for k in env if k not in used and k not in present]
    if extra:
        start = text.index("    environment:\n", text.index("  web:\n"))
        end = re.compile(r"(?m)^    \S").search(text, start + len("    environment:\n"))
        assert end, "web environment block not found"
        block = "".join(f"      {k}: {yaml_value(env[k])}\n" for k in extra)
        text = text[: end.start()] + "      # from backend/.env\n" + block + text[end.start() :]
    header = "# GENERATED with --with-env: this file holds secrets from backend/.env. Share it like a password.\n"
    return header + text, sorted(used) + extra


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path.home() / "Desktop" / "dataviv_ai_os-workflow-app.zip"))
    ap.add_argument("--with-env", action="store_true", help="carry backend/.env (secrets!) into the zip")
    args = ap.parse_args()
    out = Path(args.out).expanduser()

    print("[1/3] collecting files", flush=True)
    items = files()
    missing = [a for a, s in items if not s.exists()]
    if missing:
        sys.exit(f"missing: {missing}")
    print(f"      {len(items)} files", flush=True)

    print("[2/3] scanning for secrets", flush=True)
    problems = []
    for arc, src in items:
        data = src.read_bytes()
        for rx, what in SECRETS:
            if rx.search(data):
                problems.append(f"{arc}: looks like {what}")
    if problems:
        sys.exit("refusing to build the zip:\n  " + "\n  ".join(problems))
    print("      none found in the project files", flush=True)

    generated: dict[str, bytes] = {}
    if args.with_env:
        env, notes = platform_env()
        compose, names = render_compose((ROOT / PLACED["docker-compose.yml"]).read_text(encoding="utf-8"), env)
        dotenv = "".join(f"{k}={json.dumps(v)}\n" for k, v in env.items())
        generated = {".env": dotenv.encode(), "docker-compose.yml": compose.encode()}
        items = [(a, p) for a, p in items if a != "docker-compose.yml"]
        print(f"      --with-env: {len(env)} settings from backend/.env -> .env + docker-compose.yml", flush=True)
        print("      written into compose: " + ", ".join(names), flush=True)
        for n in notes:
            print("      " + n, flush=True)

    print(f"[3/3] writing {out}", flush=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    total = len(items) + len(generated)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for arc, data in generated.items():
            z.writestr(arc, data)
        for i, (arc, src) in enumerate(items, start=len(generated) + 1):
            z.write(src, arc)
            if i % 50 == 0 or i == total:
                bar = "#" * (30 * i // total)
                print(f"\r      [{bar:<30}] {i}/{total}", end="", flush=True)
    if generated:
        out.chmod(0o600)  # holds secrets: readable by you only
    kind = "WITH SECRETS from backend/.env" if generated else "no secrets"
    print(f"\n      {out.stat().st_size / 1e6:.1f} MB, top level: docker-compose.yml + node.yaml (application), {kind}")


if __name__ == "__main__":
    main()
