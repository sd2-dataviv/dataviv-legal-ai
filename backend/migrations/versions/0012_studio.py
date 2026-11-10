"""Drafting studio: firm-wide general documents (contracts, notices, letters) written from templates.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-10
"""

from pathlib import Path

from alembic import op
from sqlalchemy.util import await_only

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run_script(name: str) -> None:
    raw = op.get_bind().connection.dbapi_connection.driver_connection  # type: ignore[union-attr]
    await_only(raw.execute((SQL_DIR / name).read_text(encoding="utf-8")))


def upgrade() -> None:
    _run_script("0012_studio.sql")


def downgrade() -> None:
    _run_script("0012_studio_down.sql")
