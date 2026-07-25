"""Add an explicit temporal assignment for annual period context.

Revision ID: 20260724_0009
Revises: 20260724_0008_governance
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260724_0009"
down_revision: str | None = "20260724_0008_governance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE temporal_assignment ADD VALUE IF NOT EXISTS 'period_context'")


def downgrade() -> None:
    # PostgreSQL enum values cannot be safely removed while dependent rows may exist.
    pass
