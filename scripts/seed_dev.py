"""Seed a dev firm, users and sample matters, then print dev tokens for each user.

Run from repo root:  python scripts/seed_dev.py
Uses the OWNER connection (bypasses RLS) — dev only.
"""

import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from tqdm import tqdm  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.security import issue_dev_token  # noqa: E402
from app.modules.workflows.templates import PLAYBOOK_TEMPLATES  # noqa: E402

FIRM_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
OTHER_FIRM_ID = uuid.UUID("00000000-0000-4000-8000-000000000002")  # for cross-firm isolation checks
USERS = [
    ("partner", "Asha Sharma", "asha@sharma-associates.in"),
    ("associate", "Rohan Gupta", "rohan@sharma-associates.in"),
    ("paralegal", "Neha Verma", "neha@sharma-associates.in"),
    ("admin", "IT Admin", "admin@sharma-associates.in"),
]
MATTERS = [
    ("State v. Kumar — bail application", "Vikram Kumar", "Delhi High Court", "DLHC010230802020"),
    (
        "Acme Ltd v. Zenith Pvt Ltd — commercial suit",
        "Acme Ltd",
        "Delhi High Court (Commercial Division)",
        None,
    ),
    ("Share purchase agreement — Project Lotus", "Lotus Holdings", None, None),
]


async def main() -> None:
    engine = create_async_engine(get_settings().database_owner_url)
    tokens: list[tuple[str, str]] = []
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO firms (id, name) VALUES (:id, 'Sharma & Associates') ON CONFLICT DO NOTHING"),
            {"id": FIRM_ID},
        )
        ids: dict[str, uuid.UUID] = {}
        for role, name, email in tqdm(USERS, desc="Users", unit="user"):
            uid = await conn.scalar(
                text(
                    "INSERT INTO users (firm_id, email, name, role) VALUES (:f, :e, :n, :r) "
                    "ON CONFLICT (firm_id, email) DO UPDATE SET name = EXCLUDED.name RETURNING id"
                ),
                {"f": FIRM_ID, "e": email, "n": name, "r": role},
            )
            ids[role] = uid
            token = issue_dev_token(uid, FIRM_ID, role, email)  # type: ignore[arg-type]
            tokens.append((f"{role:<10} {email}", token))
        existing = await conn.scalar(text("SELECT count(*) FROM matters WHERE firm_id = :f"), {"f": FIRM_ID})
        to_seed = [] if existing else MATTERS  # re-running only refreshes tokens
        for title, client, forum, cnr in tqdm(to_seed, desc="Matters", unit="matter"):
            mid = await conn.scalar(
                text(
                    "INSERT INTO matters (firm_id, title, client_name, forum, cnr, created_by) "
                    "VALUES (:f, :t, :c, :fo, :cnr, :u) RETURNING id"
                ),
                {"f": FIRM_ID, "t": title, "c": client, "fo": forum, "cnr": cnr, "u": ids["partner"]},
            )
            for role, mrole in (("partner", "lead"), ("associate", "member")):
                await conn.execute(
                    text(
                        "INSERT INTO matter_members (matter_id, user_id, firm_id, matter_role) "
                        "VALUES (:m, :u, :f, :r)"
                    ),
                    {"m": mid, "u": ids[role], "f": FIRM_ID, "r": mrole},
                )
        # The sample playbooks, so the contract-review workflow can be tried straight away (Step 4).
        if not await conn.scalar(text("SELECT count(*) FROM playbooks WHERE firm_id = :f"), {"f": FIRM_ID}):
            for t in PLAYBOOK_TEMPLATES:
                await conn.execute(
                    text(
                        "INSERT INTO playbooks (firm_id, name, contract_type, description, clauses, created_by, "
                        "updated_by) VALUES (:f, :n, :c, :d, CAST(:cl AS jsonb), :u, :u)"
                    ),
                    {"f": FIRM_ID, "n": t["name"], "c": t["contract_type"], "d": t["description"],
                     "cl": json.dumps(t["clauses"]), "u": ids["partner"]},
                )
        # A second firm with one partner and one matter, to test that firms never see each other's data.
        await conn.execute(
            text("INSERT INTO firms (id, name) VALUES (:id, 'Mehta Legal LLP') ON CONFLICT DO NOTHING"),
            {"id": OTHER_FIRM_ID},
        )
        other = await conn.scalar(
            text(
                "INSERT INTO users (firm_id, email, name, role) VALUES (:f, 'kavya@mehtalegal.in', 'Kavya Mehta', "
                "'partner') ON CONFLICT (firm_id, email) DO UPDATE SET name = EXCLUDED.name RETURNING id"
            ),
            {"f": OTHER_FIRM_ID},
        )
        if not await conn.scalar(text("SELECT count(*) FROM matters WHERE firm_id = :f"), {"f": OTHER_FIRM_ID}):
            mid = await conn.scalar(
                text(
                    "INSERT INTO matters (firm_id, title, client_name, forum, created_by) VALUES "
                    "(:f, 'Mehta v. Union of India - writ petition', 'R. Mehta', 'Supreme Court of India', :u) "
                    "RETURNING id"
                ),
                {"f": OTHER_FIRM_ID, "u": other},
            )
            await conn.execute(
                text("INSERT INTO matter_members VALUES (:m, :u, :f, 'lead')"),
                {"m": mid, "u": other, "f": OTHER_FIRM_ID},
            )
        tokens.append(("partner    kavya@mehtalegal.in (other firm)", issue_dev_token(other, OTHER_FIRM_ID, "partner", "kavya@mehtalegal.in")))
    await engine.dispose()
    print("\nDev tokens (paste into Swagger 'Authorize' at http://127.0.0.1:8010/docs):\n")
    for who, tok in tokens:
        print(f"{who}\n  {tok}\n")


if __name__ == "__main__":
    asyncio.run(main())
