"""Foundation: firms, users, matters, members, ethical walls, jobs, audit + RLS.

Revision ID: 0001
Revises:
Create Date: 2026-10-09

SQL lives in migrations/sql/ so it can be reviewed and tested as plain SQL.
"""

from pathlib import Path

from alembic import op
from sqlalchemy.util import await_only

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run_script(name: str) -> None:
    """Run a multi-statement SQL file. asyncpg's prepared statements reject multiple commands,
    so the script goes through the driver's simple-query protocol on the same transaction."""
    raw = op.get_bind().connection.dbapi_connection.driver_connection  # type: ignore[union-attr]
    await_only(raw.execute((SQL_DIR / name).read_text()))


def upgrade() -> None:
    _run_script("0001_foundation.sql")


def downgrade() -> None:
    _run_script("0001_foundation_down.sql")
