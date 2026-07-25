"""Ledger publication batches so long runs resume instead of restarting.

Revision ID: 20260726_0013
Revises: 20260725_0012
Create Date: 2026-07-26
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260726_0013"
down_revision = "20260725_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sa.Enum(
        "running", "completed", "interrupted", name="batch_run_status"
    ).create(op.get_bind(), checkfirst=True)
    sa.Enum(
        "published", "unchanged", "failed", "skipped", name="batch_entry_status"
    ).create(op.get_bind(), checkfirst=True)
    # The types are created explicitly above; create_table must not try again.
    batch_run_status = postgresql.ENUM(
        "running",
        "completed",
        "interrupted",
        name="batch_run_status",
        create_type=False,
    )
    batch_entry_status = postgresql.ENUM(
        "published",
        "unchanged",
        "failed",
        "skipped",
        name="batch_entry_status",
        create_type=False,
    )

    op.create_table(
        "publication_batch_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column(
            "status", batch_run_status, nullable=False, server_default="running"
        ),
        sa.Column(
            "requested",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "publication_batch_entries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "batch_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("publication_batch_runs.id"),
            nullable=False,
        ),
        sa.Column("profile_date", sa.Date, nullable=False),
        sa.Column("status", batch_entry_status, nullable=False),
        sa.Column("detail", sa.Text, nullable=True),
        sa.Column(
            "publication_manifest_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("publication_manifests.id"),
            nullable=True,
        ),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "batch_run_id", "profile_date", name="publication_batch_entries_unique"
        ),
    )
    op.create_index(
        "publication_batch_entries_status_idx",
        "publication_batch_entries",
        ["batch_run_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "publication_batch_entries_status_idx",
        table_name="publication_batch_entries",
    )
    op.drop_table("publication_batch_entries")
    op.drop_table("publication_batch_runs")
    sa.Enum(name="batch_entry_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="batch_run_status").drop(op.get_bind(), checkfirst=True)
