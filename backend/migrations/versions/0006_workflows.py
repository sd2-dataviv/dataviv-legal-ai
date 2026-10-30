"""Workflows: playbooks, versioned definitions, durable runs, steps, approvals.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-09
"""

from pathlib import Path

from alembic import op
from sqlalchemy.util import await_only

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run_script(name: str) -> None:
    raw = op.get_bind().connection.dbapi_connection.driver_connection  # type: ignore[union-attr]
    await_only(raw.execute((SQL_DIR / name).read_text(encoding="utf-8")))


def upgrade() -> None:
    _run_script("0006_workflows.sql")


def downgrade() -> None:
    _run_script("0006_workflows_down.sql")
