"""Evaluation harness (NFR-03): run the benchmark suites and gate a release on the results.

    cd backend
    python -m evals.run                      # both suites, with the model in backend/.env (Groq)
    python -m evals.run --suite assistant --limit 5
    python -m evals.run --backend fake       # offline smoke run (no key, no cost)

It uses the TEST database (rebuilt from the migrations, never your dev data): it seeds an evaluation
firm, uploads the corpus in evals/corpus, asks every question through the real API, fills a review
table, scores everything and writes logs/evals/eval-<time>.md and .json. Exit code 1 if a gate fails.
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
EVALS = Path(__file__).resolve().parent
TEST_DB = "postgresql+asyncpg://app_user:app_user_dev_only@localhost:5432/dataviv_test"
TEST_DB_OWNER = "postgresql+asyncpg://dataviv:dataviv_dev_only@localhost:5432/dataviv_test"

RunJobs = Callable[[], Awaitable[int]]
Progress = Callable[[str], None]


def load_suite(name: str) -> dict[str, Any]:
    return json.loads((EVALS / "suites" / f"{name}.json").read_text(encoding="utf-8"))


def sse_events(body: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in fields and "data" in fields:
            out.append((fields["event"], json.loads(fields["data"])))
    return out


async def run_suites(
    client: Any,
    headers: dict[str, str],
    suites: list[str],
    run_jobs: RunJobs,
    *,
    limit: int | None = None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Run suites through the HTTP API with an authenticated client. Used by the CLI and by tests."""
    from evals.scoring import CellResult, score_case, score_cell, summarise_assistant, summarise_review

    say = progress or (lambda _msg: None)
    r = await client.post(
        "/v1/matters",
        json={"title": f"Evaluation {uuid.uuid4().hex[:6]}", "client_name": "Benchmark"},
        headers=headers,
    )
    r.raise_for_status()
    matter_id = r.json()["id"]
    corpus = sorted((EVALS / "corpus").glob("*.txt"))
    files = [("files", (f.name, f.read_bytes(), "text/plain")) for f in corpus]
    up = await client.post(f"/v1/matters/{matter_id}/documents", files=files, headers=headers)
    up.raise_for_status()
    say(f"Uploaded {len(corpus)} corpus documents; indexing")
    await run_jobs()
    by_name = {f["filename"]: f["document_id"] for f in up.json()["files"] if f.get("document_id")}
    names = {v: k for k, v in by_name.items()}
    report: dict[str, Any] = {
        "started": datetime.now().isoformat(timespec="seconds"),
        "matter_id": matter_id,
        "suites": {},
    }
    models = (await client.get("/v1/assistant/models", headers=headers)).json()
    report["backend"], report["model"] = models.get("backend"), models.get("default")

    if "assistant" in suites:
        suite = load_suite("assistant")
        cases = suite["cases"][:limit] if limit else suite["cases"]
        results = []
        for i, case in enumerate(cases, 1):
            say(f"Assistant {i}/{len(cases)}: {case['id']}")
            t = await client.post(f"/v1/matters/{matter_id}/threads", json={}, headers=headers)
            t.raise_for_status()
            body: dict[str, Any] = {"content": case["q"], "mode": case.get("mode", "ask")}
            if case.get("draft_kind"):
                body["draft_kind"] = case["draft_kind"]
            if case.get("documents"):
                body["document_ids"] = [by_name[d] for d in case["documents"]]
            message: dict[str, Any] | None = None
            error: str | None = None
            for attempt in range(4):
                resp = await client.post(
                    f"/v1/threads/{t.json()['id']}/messages", json=body, headers=headers, timeout=300
                )
                if resp.status_code != 200:
                    error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    break
                events = sse_events(resp.text)
                kind, data = events[-1] if events else ("error", {"detail": "empty stream"})
                if kind == "done":
                    message, error = data, None
                    break
                error = str(data.get("detail"))
                if (
                    data.get("status") == 429 and attempt < 3
                ):  # wait out the provider's rate limit, then retry
                    wait = min(float(data.get("retry_after") or 20), 90.0)
                    say(f"  rate limited; waiting {wait:.0f} s")
                    await asyncio.sleep(wait)
                    continue
                break
            result = score_case(case, message or {}, names)
            result.error = error
            results.append(result)
        report["suites"]["assistant"] = {
            "summary": summarise_assistant(results, suite["gates"]),
            "cases": [x.as_dict() for x in results],
        }

    if "review" in suites:
        suite = load_suite("review")
        say("Review table: creating and filling")
        cols = [
            {k: c[k] for k in ("label", "question", "answer_type")} | {"choices": c.get("choices", [])}
            for c in suite["columns"]
        ]
        tr = await client.post(
            f"/v1/matters/{matter_id}/review-tables",
            json={
                "title": "Benchmark review",
                "columns": cols,
                "document_ids": [by_name[d] for d in suite["documents"]],
            },
            headers=headers,
        )
        tr.raise_for_status()
        table_id = tr.json()["id"]
        (
            await client.post(f"/v1/review-tables/{table_id}/run", json={"scope": "all"}, headers=headers)
        ).raise_for_status()
        while await run_jobs():  # a run re-queues itself every few documents
            pass
        grid = (await client.get(f"/v1/review-tables/{table_id}", headers=headers)).json()
        col_by_label = {c["label"]: c for c in grid["column_list"]}
        cells = {(c["document_id"], c["column_id"]): c for c in grid["cells"]}
        scored: list[CellResult] = []
        for doc, expectations in suite["expected"].items():
            for spec in suite["columns"]:
                col = col_by_label[spec["label"]]
                cell = cells.get((by_name[doc], col["id"]), {"status": "missing"})
                expected = expectations[spec["key"]]
                ok, got = score_cell(spec["answer_type"], expected, cell)
                scored.append(
                    CellResult(
                        doc,
                        spec["label"],
                        expected,
                        cell.get("status", "missing"),
                        got,
                        ok,
                        cell.get("verified"),
                        cell.get("flag"),
                    )
                )
        t = grid["totals"]
        usage = {
            "input_tokens": t["input_tokens"],
            "output_tokens": t["output_tokens"],
            "cost_usd": t["cost_usd"],
        }
        report["suites"]["review"] = {
            "summary": summarise_review(scored, suite["gates"], usage),
            "cells": [c.__dict__ for c in scored],
        }
    report["finished"] = datetime.now().isoformat(timespec="seconds")
    report["passed"] = all(g["passed"] for s in report["suites"].values() for g in s["summary"]["gates"])
    return report


def to_markdown(report: dict[str, Any]) -> str:
    tick = {True: "pass", False: "FAIL", None: "-"}
    lines = [
        f"# Evaluation report - {report['started']}",
        "",
        f"Backend **{report.get('backend')}**, default model **{report.get('model')}**. Overall: **{'PASS' if report['passed'] else 'FAIL'}**.",
        "",
    ]
    a = report["suites"].get("assistant")
    if a:
        s = a["summary"]
        lines += ["## Assistant", "", "| Gate | Value | Threshold | Result |", "|---|---|---|---|"]
        lines += [
            f"| {g['metric']} | {g['value']} | {g['threshold']} | {tick[g['passed']]} |" for g in s["gates"]
        ]
        lines += [
            "",
            f"{s['passed']}/{s['cases']} cases passed, {s['errors']} errors. Tokens {s['input_tokens']} in / "
            f"{s['output_tokens']} out, cost ${s['cost_usd']:.4f}, mean latency {s['mean_latency_ms']} ms.",
            "",
            "| Case | Kind | Result | Correct | Cited | Abstained | Injection | Notes |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for c in a["cases"]:
            note = c["error"] or (
                c["answer"][:140].replace("|", "/").replace("\n", " ") if not c["passed"] else ""
            )
            lines.append(
                f"| {c['id']} | {c['kind']} | {tick[c['passed']]} | {tick[c['correct']]} | {tick[c['cited']]} | "
                f"{tick[c['abstained']]} | {tick[c['injection_resisted']]} | {note} |"
            )
        lines.append("")
    rv = report["suites"].get("review")
    if rv:
        s = rv["summary"]
        lines += ["## Review table", "", "| Gate | Value | Threshold | Result |", "|---|---|---|---|"]
        lines += [
            f"| {g['metric']} | {g['value']} | {g['threshold']} | {tick[g['passed']]} |" for g in s["gates"]
        ]
        lines += [
            "",
            f"Cost ${s['cost_usd']:.4f} ({s['input_tokens']} in / {s['output_tokens']} out tokens).",
            "",
        ]
        lines += [
            "| Document | Column | Expected | Got | Result | Verified | Flag |",
            "|---|---|---|---|---|---|---|",
        ]
        for c in rv["cells"]:
            lines.append(
                f"| {c['document']} | {c['column']} | {', '.join(str(e) for e in c['expected'])} | {c['got'] or c['status']} | "
                f"{tick[c['correct']]} | {tick[c['verified']]} | {c['flag'] or ''} |"
            )
    return "\n".join(lines) + "\n"


async def _reset_test_database(owner_url: str) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    sql_dir = BACKEND / "migrations" / "sql"
    engine = create_async_engine(owner_url)
    downs = sorted(sql_dir.glob("*_down.sql"), reverse=True)
    ups = sorted(p for p in sql_dir.glob("*.sql") if not p.name.endswith("_down.sql"))
    async with engine.connect() as conn:
        raw = await conn.get_raw_connection()
        for f in [*downs, *ups]:
            await raw.driver_connection.execute(f.read_text(encoding="utf-8"))  # type: ignore[union-attr]
        await conn.commit()
    await engine.dispose()


async def _seed(owner_url: str) -> dict[str, str]:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.security import issue_dev_token

    firm, user = uuid.uuid4(), uuid.uuid4()
    engine = create_async_engine(owner_url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO firms (id, name) VALUES (:f, 'Evaluation firm')"), {"f": firm})
        await conn.execute(
            text(
                "INSERT INTO users (id, firm_id, email, name, role) VALUES (:u, :f, 'eval@bench.local', 'Benchmark', 'partner')"
            ),
            {"u": user, "f": firm},
        )
    await engine.dispose()
    return {"Authorization": f"Bearer {issue_dev_token(user, firm, 'partner', 'eval@bench.local')}"}


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suite", choices=["assistant", "review", "all"], default="all")
    ap.add_argument(
        "--backend", choices=["groq", "ollama", "fake"], help="default: DATAVIV_LLM_BACKEND from backend/.env"
    )
    ap.add_argument("--limit", type=int, help="only the first N assistant cases")
    ap.add_argument("--database-url", default=os.environ.get("DATAVIV_EVAL_DATABASE_URL", TEST_DB))
    ap.add_argument(
        "--database-owner-url", default=os.environ.get("DATAVIV_EVAL_DATABASE_OWNER_URL", TEST_DB_OWNER)
    )
    args = ap.parse_args(argv)

    os.environ.update(
        {
            "DATAVIV_ENV": "test",
            "DATAVIV_DATABASE_URL": args.database_url,
            "DATAVIV_DATABASE_OWNER_URL": args.database_owner_url,
            "DATAVIV_RUN_WORKER_IN_API": "false",
            "DATAVIV_STORAGE_DIR": tempfile.mkdtemp(prefix="dataviv-eval-"),
            "DATAVIV_OCR_URL": "",
        }
    )
    if args.backend:
        os.environ["DATAVIV_LLM_BACKEND"] = args.backend
    sys.path.insert(0, str(BACKEND))

    from httpx import ASGITransport, AsyncClient
    from tqdm import tqdm

    from app.db.session import dispose_engine
    from app.main import create_app
    from app.worker import run_pending

    suites = ["assistant", "review"] if args.suite == "all" else [args.suite]
    print("Preparing the test database (your dev data is not touched)...", flush=True)
    await _reset_test_database(args.database_owner_url)
    headers = await _seed(args.database_owner_url)
    all_cases = len(load_suite("assistant")["cases"])
    n_cases = (min(args.limit, all_cases) if args.limit else all_cases) if "assistant" in suites else 0
    bar = tqdm(total=n_cases + (1 if "review" in suites else 0) + 1, unit="step", desc="Evaluating")

    def progress(msg: str) -> None:
        if msg.startswith(("Assistant", "Review", "Uploaded")):
            bar.update(1)
        bar.set_postfix_str(msg[:60])

    started = time.perf_counter()
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://eval", timeout=300
    ) as client:
        report = await run_suites(client, headers, suites, run_pending, limit=args.limit, progress=progress)
    bar.close()
    await dispose_engine()
    report["seconds"] = round(time.perf_counter() - started)

    out_dir = ROOT / "logs" / "evals"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    md, js = out_dir / f"eval-{stamp}.md", out_dir / f"eval-{stamp}.json"
    md.write_text(to_markdown(report), encoding="utf-8")
    js.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    for name, s in report["suites"].items():
        print(f"\n{name}:")
        for g in s["summary"]["gates"]:
            print(
                f"  {'PASS' if g['passed'] else 'FAIL'}  {g['metric']:<26} {g['value']!s:>8}  (gate {g['threshold']})"
            )
    print(f"\nReport: {md}\nOverall: {'PASS' if report['passed'] else 'FAIL'} in {report['seconds']} s")
    print("EVAL_PASS" if report["passed"] else "EVAL_FAIL")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
