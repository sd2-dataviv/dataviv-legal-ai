"""Embed every passage not yet in the vector store (Step 1c). Run from the repo root:

    .venv/bin/python scripts/reindex_vectors.py            # all firms, pending passages only
    .venv/bin/python scripts/reindex_vectors.py --status   # what is pending, change nothing
    .venv/bin/python scripts/reindex_vectors.py --all      # re-embed everything (e.g. after a model swap)

Use it after switching semantic search on for existing documents, after the embedding service or
Qdrant was down during uploads, or after changing DATAVIV_EMBEDDINGS_MODEL. Safe to stop and re-run:
it commits document by document. Embedding runs per firm as app_user, so RLS applies throughout.
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tqdm import tqdm

from app.core.config import get_settings
from app.db.session import dispose_engine, tenant_session
from app.modules.vault import hybrid


async def main(args: argparse.Namespace) -> int:
    s = get_settings()
    p = hybrid.parts()
    if p is None:
        print("Semantic search is off. In backend/.env set DATAVIV_EMBEDDINGS_BACKEND (local, http or ollama)")
        print("and DATAVIV_VECTOR_STORE=qdrant, then run this again.")
        return 2
    emb, store = p
    print(f"model {emb.name} ({emb.dim} dims) -> {store.name} collection {store.collection}")

    owner = create_async_engine(s.database_owner_url)
    async with owner.connect() as conn:  # firm list only; the passages are read per firm under RLS
        firms = (await conn.execute(text("SELECT id, name FROM firms ORDER BY name"))).all()
    await owner.dispose()

    plan = []
    for fid, name in firms:
        async with tenant_session(fid) as db:
            if args.all and not args.status:
                await db.execute(text("UPDATE chunks SET embedding_model = NULL"))
            pending = await hybrid.pending_count(db, emb.name)
        plan.append((fid, name, pending))
        print(f"  {name}: {pending} passage(s) to embed")
    total = sum(x[2] for x in plan)
    if args.status or total == 0:
        print("Nothing to do." if total == 0 else "Status only - nothing changed.")
        return 0

    started = time.monotonic()
    with tqdm(total=total, unit="passage", desc="Embedding", dynamic_ncols=True) as bar:
        for fid, name, pending in plan:
            if not pending:
                continue
            bar.set_postfix_str(name[:30])
            base = bar.n

            def advance(done: int, _total: int, base: int = base) -> None:
                bar.update(base + done - bar.n)

            await hybrid.backfill(fid, advance)
    secs = time.monotonic() - started
    print(f"Embedded {total} passage(s) in {secs:.0f}s ({total / max(secs, 0.001):.1f}/s).")
    await dispose_engine()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--status", action="store_true", help="show what is pending and exit")
    ap.add_argument("--all", action="store_true", help="re-embed every passage, not only pending ones")
    sys.exit(asyncio.run(main(ap.parse_args())))
