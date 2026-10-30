"""Print the latest agent runs with every step, so a failed run can be diagnosed from the log alone.

Run from repo root:  python scripts/agent_report.py [N]     (Windows: scripts\\windows\\agent_report.bat)
Uses the OWNER connection (bypasses RLS) - dev only. Prints no secrets.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.core.config import get_settings  # noqa: E402


def cut(value: object, n: int = 600) -> str:
    s = " ".join(str(value or "").split())
    return s if len(s) <= n else s[:n] + " ..."


async def main(limit: int) -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]  # quotes and dashes in documents
    s = get_settings()
    print(f"LLM backend: {s.llm_backend}   default model: {s.llm_default_model}")
    engine = create_async_engine(s.database_owner_url)
    async with engine.connect() as conn:
        runs = (await conn.execute(text(
            "SELECT id, slug, status, goal, error, steps_used, max_steps, cost_usd, max_cost_usd, parent_run_id,"
            " created_at FROM agent_runs ORDER BY created_at DESC LIMIT :n"), {"n": limit})).all()
        if not runs:
            print("No agent runs yet.")
        for r in runs:
            print("=" * 100)
            print(f"run {r.id}  agent={r.slug}  status={r.status}  steps={r.steps_used}/{r.max_steps}"
                  f"  cost=${float(r.cost_usd):.5f}/${float(r.max_cost_usd):.2f}"
                  f"{'  (subagent)' if r.parent_run_id else ''}  {r.created_at:%Y-%m-%d %H:%M}")
            print(f"  goal:  {cut(r.goal, 200)}")
            if r.error:
                print(f"  ERROR: {r.error}")
            job = (await conn.execute(text(
                "SELECT j.status, j.error, j.attempts, j.current_step FROM jobs j JOIN agent_runs a ON a.job_id = j.id"
                " WHERE a.id = :r"), {"r": r.id})).first()
            if job:
                print(f"  job:   status={job.status} attempts={job.attempts} step={job.current_step!r}"
                      + (f" error={job.error}" if job.error else ""))
            steps = (await conn.execute(text(
                "SELECT position, tool, status, thought, arguments, observation, error, model, input_tokens,"
                " output_tokens FROM agent_steps WHERE run_id = :r ORDER BY position"), {"r": r.id})).all()
            for st in steps:
                print(f"  [{st.position + 1}] {st.tool} -> {st.status}  ({st.model or '-'},"
                      f" {st.input_tokens}+{st.output_tokens} tokens)")
                if st.thought:
                    print(f"      thought: {cut(st.thought, 300)}")
                print(f"      args:    {cut(json.dumps(st.arguments), 300)}")
                if st.observation:
                    print(f"      result:  {cut(st.observation, 500)}")
                if st.error:
                    print(f"      error:   {cut(st.error, 500)}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 8))
