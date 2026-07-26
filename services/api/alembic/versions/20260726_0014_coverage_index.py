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
    _backfill_existing_archive()


def _backfill_existing_archive() -> None:
    """Index what is already published.

    An upgrade over a populated archive would otherwise leave every date
    reporting coverage_not_indexed, and re-running the publishers does not
    heal it for content that has not changed. Quality floors are left null
    because they live in the artifacts, not the database; `make
    rebuild-coverage` fills them in and is part of the documented deploy
    flow.
    """
    op.execute(
        """
        INSERT INTO coverage_entries (
            profile_date, profile_type, publication_manifest_id,
            publication_tier, has_recorded_event, sections
        )
        SELECT
            m.profile_date,
            m.profile_type,
            m.id,
            m.publication_tier,
            COALESCE(counts.recorded, 0) > 0,
            -- Every supported key, zero-filled: _section_counts always
            -- emits all seven, and a row's shape must not reveal whether
            -- it was backfilled or rebuilt.
            (
                '{"recorded_on_this_date": 0, "typical_day_in_this_year": 0,
                  "wider_historical_context": 0, "curated_claims": 0,
                  "derived_comparisons": 0, "wonder_and_progress": 0,
                  "evidence_notes": 0}'::jsonb
                || COALESCE(counts.sections, '{}'::jsonb)
            )
        FROM publication_manifests AS m
        JOIN day_profiles AS d ON d.publication_manifest_id = m.id
        JOIN (
            -- Newest published version that actually has a profile row:
            -- main.py joins day_profiles before ordering, so an incomplete
            -- newest manifest does not make the date unservable.
            SELECT pm.profile_date, MAX(pm.version) AS version
            FROM publication_manifests AS pm
            JOIN day_profiles AS dp ON dp.publication_manifest_id = pm.id
            WHERE pm.status = 'published'
            GROUP BY pm.profile_date
        ) AS newest
          ON newest.profile_date = m.profile_date AND newest.version = m.version
        LEFT JOIN LATERAL (
            SELECT
                jsonb_object_agg(section, count) AS sections,
                SUM(count) FILTER (
                    WHERE section = 'recorded_on_this_date'
                ) AS recorded
            FROM (
                SELECT
                    split_part(e.statement_path, '/', 3) AS section,
                    COUNT(*) AS count
                FROM publication_statement_evidence AS e
                WHERE e.publication_manifest_id = m.id
                  -- Same keys the publisher counts. A path outside the
                  -- contract vocabulary would otherwise appear only in
                  -- backfilled rows, so identical archive state would
                  -- describe itself differently depending on how it was
                  -- indexed.
                  AND split_part(e.statement_path, '/', 3) IN (
                      'recorded_on_this_date', 'typical_day_in_this_year',
                      'wider_historical_context', 'curated_claims',
                      'derived_comparisons', 'wonder_and_progress',
                      'evidence_notes'
                  )
                GROUP BY 1
            ) AS per_section
        ) AS counts ON TRUE
        WHERE m.status = 'published'
        ON CONFLICT (profile_date) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("coverage_entries_recorded_date_idx", table_name="coverage_entries")
    op.drop_index("coverage_entries_tier_date_idx", table_name="coverage_entries")
    op.drop_table("coverage_entries")
