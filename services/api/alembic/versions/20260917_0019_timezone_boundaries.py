"""Timezone boundary polygons for coordinates->timezone resolution (A3).

Revision ID: 20260917_0019
Revises: 20260803_0018
Create Date: 2026-09-17
"""

from alembic import op

revision = "20260917_0019"
down_revision = "20260803_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE timezone_boundaries (
            id UUID PRIMARY KEY,
            tzid TEXT NOT NULL UNIQUE,
            dataset_version TEXT NOT NULL,
            boundary_geometry geometry(MULTIPOLYGON, 4326) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    # GiST index so ST_Covers point lookups use the spatial index rather than a
    # sequential scan over every zone polygon.
    op.execute(
        "CREATE INDEX timezone_boundaries_geometry_gist "
        "ON timezone_boundaries USING gist (boundary_geometry)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS timezone_boundaries")
