"""Semantic search: chunks.embedding_model.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-15
"""

from pathlib import Path

from alembic import op
from sqlalchemy.util import await_only

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run_script(name: str) -> None:
    raw = op.get_bind().connection.dbapi_connection.driver_connection  # type: ignore[union-attr]
    await_only(raw.execute((SQL_DIR / name).read_text(encoding="utf-8")))


def upgrade() -> None:
    _run_script("0008_semantic.sql")


def downgrade() -> None:
    _run_script("0008_semantic_down.sql")
