"""Record how much each published profile actually offers.

Revision ID: 20260725_0012
Revises: 20260724_0011
Create Date: 2026-07-25

Existing manifests are backfilled from their immutable statement-evidence
rows, which record the section each published statement occupies, so the
tier of already-published profiles is derived from evidence rather than
assumed.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260725_0012"
down_revision = "20260724_0011"
branch_labels = None
depends_on = None

RECORDED_PREFIX = "/sections/recorded_on_this_date/"
EDITORIAL_PREFIXES = (
    "/sections/curated_claims/",
    "/sections/derived_comparisons/",
    "/sections/wonder_and_progress/",
)


def upgrade() -> None:
    publication_tier = sa.Enum(
        "context_only",
        "partially_enriched",
        "reviewed_enriched",
        name="publication_tier",
    )
    publication_tier.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "publication_manifests",
        sa.Column(
            "publication_tier",
            publication_tier,
            nullable=False,
            server_default="context_only",
        ),
    )
    op.create_index(
        "publication_manifests_tier_idx",
        "publication_manifests",
        ["publication_tier"],
    )

    # Published manifests are immutable to application code
    # (publication_manifests_final_immutable). A schema migration backfilling
    # derived metadata is exactly the case that guard is not defending
    # against, so it is suspended for the backfill and restored immediately.
    op.execute(
        "ALTER TABLE publication_manifests "
        "DISABLE TRIGGER publication_manifests_final_immutable"
    )

    # starts_with is an exact prefix test: LIKE would treat every underscore
    # in these section names as a single-character wildcard and could
    # overstate a lookalike path's tier.
    editorial_predicate = " OR ".join(
        f"starts_with(evidence.statement_path, '{prefix}')"
        for prefix in EDITORIAL_PREFIXES
    )
    op.execute(
        f"""
        UPDATE publication_manifests AS manifests
        SET publication_tier = 'partially_enriched'
        WHERE EXISTS (
            SELECT 1 FROM publication_statement_evidence AS evidence
            WHERE evidence.publication_manifest_id = manifests.id
              AND ({editorial_predicate})
        )
        """
    )
    op.execute(
        f"""
        UPDATE publication_manifests AS manifests
        SET publication_tier = 'reviewed_enriched'
        WHERE EXISTS (
            SELECT 1 FROM publication_statement_evidence AS evidence
            WHERE evidence.publication_manifest_id = manifests.id
              AND starts_with(evidence.statement_path, '{RECORDED_PREFIX}')
        )
        """
    )


    op.execute(
        "ALTER TABLE publication_manifests "
        "ENABLE TRIGGER publication_manifests_final_immutable"
    )


def downgrade() -> None:
    op.drop_index("publication_manifests_tier_idx", table_name="publication_manifests")
    op.drop_column("publication_manifests", "publication_tier")
    sa.Enum(name="publication_tier").drop(op.get_bind(), checkfirst=True)
