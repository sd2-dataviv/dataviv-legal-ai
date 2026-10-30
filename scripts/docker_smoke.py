"""Smoke test of the containerised stack: health, console, upload -> ingest, hybrid search, live drafts + AI."""

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

BASE = os.environ.get("DATAVIV_BASE", "127.0.0.1:8080")
T0 = time.monotonic()
SPA = """SHARE PURCHASE AGREEMENT - PROJECT LOTUS

1. Parties. Lotus Holdings Private Limited (the Seller) and Acme Capital LLP (the Buyer).

7. Representations. The Seller represents that all statutory filings with the Registrar of Companies are
up to date and that no litigation is pending against the Company.

12. Termination. If the Seller breaches a representation and fails to cure the breach within fifteen (15)
days of written notice, the Buyer may terminate this Agreement by written notice under this Clause 12.

15. Governing law. This Agreement is governed by the laws of India; courts at New Delhi have jurisdiction.
"""
DRAFT = """To,
Lotus Holdings Private Limited

Subject: Notice of breach of the Share Purchase Agreement

1. Our client has found that the statutory filings are not up to date.
2. This breaches the Seller's representations.
3. Our client may end the agreement if this is not fixed.

P.S. Please note a 15-day cure period applies.
"""


def say(msg: str) -> None:
    print(f"[{time.monotonic() - T0:6.1f}s] {msg}", flush=True)


def main() -> None:
    http = httpx.Client(base_url=f"http://{BASE}", timeout=300)
    say("1/5 health and console")
    assert http.get("/health").status_code == 200
    ui = http.get("/ui/")
    assert ui.status_code == 200 and "<div id=\"root\"" in ui.text, ui.text[:200]
    say("    /health 200, /ui/ serves the built console")

    ids = {i["name"]: i for i in http.get("/v1/dev/identities").json()}
    h = {"Authorization": f"Bearer {ids['Rohan Gupta']['access_token']}"}
    matter = next(m for m in http.get("/v1/matters", headers=h).json() if "Lotus" in m["title"])
    st = http.get("/v1/search/status", headers=h).json()
    say(f"    signed in as Rohan; semantic search enabled={st['enabled']} ({st['embeddings_backend']} + {st['vector_store']})")

    say("2/5 upload the SPA and wait for ingest (first run downloads bge-m3 into the volume)")
    r = http.post(
        f"/v1/matters/{matter['id']}/documents",
        files=[("files", ("lotus-spa.txt", SPA.encode(), "text/plain"))],
        headers=h,
    )
    assert r.status_code == 202, r.text
    job = r.json().get("job_id")
    last = None
    if not job:  # a re-run: the same file is already in the matter, so nothing new to ingest
        say("    already ingested on an earlier run (duplicate upload, no new job)")
    while job:
        j = http.get(f"/v1/jobs/{job}", headers=h).json()
        line = f"{j['status']} {j['progress']}% {j['current_step'] or ''}".strip()
        if line != last:
            say(f"    job: {line}")
            last = line
        if j["status"] in ("succeeded", "failed", "done", "completed", "error"):
            break
        time.sleep(2)
    if job:
        assert j["status"] in ("succeeded", "done", "completed"), j

    say("3/5 search: by words and by meaning")
    for mode, q in (("keyword", "Registrar of Companies"), ("semantic", "how long does the seller have to fix a breach")):
        res = http.post("/v1/search", json={"query": q, "mode": mode, "matter_ids": [matter["id"]]}, headers=h).json()
        hits = res.get("results") or res.get("hits") or []
        assert hits, res
        say(f"    {mode:8} '{q}' -> {len(hits)} hit(s), top: {str(hits[0].get('snippet') or hits[0].get('text'))[:70]!r}")
    st = http.get("/v1/search/status", headers=h).json()
    say(f"    vectors in Qdrant for this firm: {st['vectors']}, pending: {st['pending_passages']}")

    say("4/5 create a draft for the live check")
    d = http.post(
        f"/v1/matters/{matter['id']}/drafts", json={"title": "Docker smoke notice", "kind": "other", "content": DRAFT}, headers=h
    ).json()
    say(f"    draft {d['id']}")

    if os.environ.get("DATAVIV_SMOKE_LIVE", "1") == "0":
        say("5/5 skipped (DATAVIV_SMOKE_LIVE=0: no model provider configured)")
        say("ALL CHECKS PASSED")
        return
    say("5/5 live co-editing, AI suggestion, approval (two users over the real WebSocket)")
    env = {**os.environ, "DATAVIV_BASE": BASE}
    rc = subprocess.call([sys.executable, str(Path(__file__).with_name("live_e2e.py")), d["id"]], env=env)
    assert rc == 0, f"live check failed ({rc})"
    say("ALL CHECKS PASSED")


main()
