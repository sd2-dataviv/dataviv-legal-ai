"""Live drafts: draft_live (Yjs working copy), draft_suggestions (AI redlines), version contributors.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-15
"""

from pathlib import Path

from alembic import op
from sqlalchemy.util import await_only

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run_script(name: str) -> None:
    raw = op.get_bind().connection.dbapi_connection.driver_connection  # type: ignore[union-attr]
    await_only(raw.execute((SQL_DIR / name).read_text(encoding="utf-8")))


def upgrade() -> None:
    _run_script("0009_live_drafts.sql")


def downgrade() -> None:
    _run_script("0009_live_drafts_down.sql")
