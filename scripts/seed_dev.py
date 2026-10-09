"""Seed a dev firm, users and sample matters, then print dev tokens for each user.

Run from repo root:  python scripts/seed_dev.py
Uses the OWNER connection (bypasses RLS) — dev only.
"""

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from tqdm import tqdm  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.security import issue_dev_token  # noqa: E402

FIRM_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
USERS = [
    ("partner", "Asha Sharma", "asha@sharma-associates.in"),
    ("associate", "Rohan Gupta", "rohan@sharma-associates.in"),
    ("paralegal", "Neha Verma", "neha@sharma-associates.in"),
    ("admin", "IT Admin", "admin@sharma-associates.in"),
]
MATTERS = [
    ("State v. Kumar — bail application", "Vikram Kumar", "Delhi High Court", "DLHC010230802020"),
    (
        "Acme Ltd v. Zenith Pvt Ltd — commercial suit", "Acme Ltd",
        "Delhi High Court (Commercial Division)", None,
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
                text("INSERT INTO users (firm_id, email, name, role) VALUES (:f, :e, :n, :r) "
                     "ON CONFLICT (firm_id, email) DO UPDATE SET name = EXCLUDED.name RETURNING id"),
                {"f": FIRM_ID, "e": email, "n": name, "r": role},
            )
            ids[role] = uid
            token = issue_dev_token(uid, FIRM_ID, role, email)  # type: ignore[arg-type]
            tokens.append((f"{role:<10} {email}", token))
        for title, client, forum, cnr in tqdm(MATTERS, desc="Matters", unit="matter"):
            mid = await conn.scalar(
                text("INSERT INTO matters (firm_id, title, client_name, forum, cnr, created_by) "
                     "VALUES (:f, :t, :c, :fo, :cnr, :u) RETURNING id"),
                {"f": FIRM_ID, "t": title, "c": client, "fo": forum, "cnr": cnr, "u": ids["partner"]},
            )
            for role, mrole in (("partner", "lead"), ("associate", "member")):
                await conn.execute(
                    text("INSERT INTO matter_members (matter_id, user_id, firm_id, matter_role) "
                         "VALUES (:m, :u, :f, :r)"),
                    {"m": mid, "u": ids[role], "f": FIRM_ID, "r": mrole},
                )
    await engine.dispose()
    print("\nDev tokens (paste into Swagger 'Authorize' at http://localhost:8000/docs):\n")
    for who, tok in tokens:
        print(f"{who}\n  {tok}\n")


if __name__ == "__main__":
    asyncio.run(main())
