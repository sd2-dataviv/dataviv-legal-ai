"""Shared API on WorkflowHub: platform_user_by_email() lookup for platform callers.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-23
"""

from pathlib import Path

from alembic import op
from sqlalchemy.util import await_only

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run_script(name: str) -> None:
    raw = op.get_bind().connection.dbapi_connection.driver_connection  # type: ignore[union-attr]
    await_only(raw.execute((SQL_DIR / name).read_text(encoding="utf-8")))


def upgrade() -> None:
    _run_script("0011_platform_identity.sql")


def downgrade() -> None:
    _run_script("0011_platform_identity_down.sql")
