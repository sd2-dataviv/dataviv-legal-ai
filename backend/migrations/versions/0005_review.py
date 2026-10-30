"""Review tables: questions across many documents, one cited answer per cell.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-09
"""

from pathlib import Path

from alembic import op
from sqlalchemy.util import await_only

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run_script(name: str) -> None:
    raw = op.get_bind().connection.dbapi_connection.driver_connection  # type: ignore[union-attr]
    await_only(raw.execute((SQL_DIR / name).read_text(encoding="utf-8")))


def upgrade() -> None:
    _run_script("0005_review.sql")


def downgrade() -> None:
    _run_script("0005_review_down.sql")
