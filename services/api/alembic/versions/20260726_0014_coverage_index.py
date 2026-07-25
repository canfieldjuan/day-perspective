"""Index the archive's richness per date.

Revision ID: 20260726_0014
Revises: 20260726_0013
Create Date: 2026-07-26
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260726_0014"
down_revision = "20260726_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "coverage_entries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("profile_date", sa.Date, nullable=False),
        sa.Column(
            "profile_type",
            postgresql.ENUM(name="profile_type", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "publication_manifest_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("publication_manifests.id"),
            nullable=False,
        ),
        sa.Column(
            "publication_tier",
            postgresql.ENUM(name="publication_tier", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "has_recorded_event",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "sections",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("quality_floor", sa.String(8), nullable=True),
        sa.Column(
            "review_status",
            sa.String(32),
            nullable=False,
            server_default="rule_selected",
        ),
        sa.Column("index_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "refreshed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("profile_date", name="coverage_entries_date_unique"),
    )
    # Navigation asks "the nearest date richer than context" constantly.
    op.create_index(
        "coverage_entries_tier_date_idx",
        "coverage_entries",
        ["publication_tier", "profile_date"],
    )
    op.create_index(
        "coverage_entries_recorded_date_idx",
        "coverage_entries",
        ["has_recorded_event", "profile_date"],
    )


def downgrade() -> None:
    op.drop_index("coverage_entries_recorded_date_idx", table_name="coverage_entries")
    op.drop_index("coverage_entries_tier_date_idx", table_name="coverage_entries")
    op.drop_table("coverage_entries")
