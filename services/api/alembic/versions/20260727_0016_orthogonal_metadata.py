"""Separate richness, review, and quality into three fields (epic #64, MD1).

One label answered three different questions at once. `reviewed_enriched`
said a profile was both rich *and* checked by a person, and the archive had
no way to say a context-only page had been reviewed, or that an enriched
page had not.

So `reviewed_enriched` becomes `enriched` — richness stops implying review —
and the two other questions get their own fields.

They live on `coverage_entries` rather than on the manifest, and are not in
the hashed payload, because they describe how content was validated rather
than what it says. A human reviewing a date, or a source being regraded,
changes neither the profile's content nor its hash, and must not force a
republication of an unchanged artifact.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260727_0016"
down_revision = "20260727_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Renaming the value keeps every existing row valid. Published artifacts
    # still carry the old string in their immutable bytes; the day endpoint
    # overlays the manifest's tier onto the served payload, so readers see
    # the new vocabulary before the archive is republished.
    op.execute(
        "ALTER TYPE publication_tier RENAME VALUE 'reviewed_enriched' TO 'enriched'"
    )
    op.execute(
        "CREATE TYPE review_status AS ENUM "
        "('automated_only','review_pending','human_reviewed')"
    )
    op.execute(
        "CREATE TYPE quality_floor AS ENUM ('A','B','C','D','not_assessed')"
    )
    op.add_column(
        "coverage_entries",
        sa.Column(
            "review_status",
            sa.Enum(
                "automated_only",
                "review_pending",
                "human_reviewed",
                name="review_status",
                create_type=False,
            ),
            nullable=False,
            server_default="automated_only",
        ),
    )
    op.add_column(
        "coverage_entries",
        sa.Column(
            "quality_floor",
            sa.Enum(
                "A",
                "B",
                "C",
                "D",
                "not_assessed",
                name="quality_floor",
                create_type=False,
            ),
            nullable=False,
            server_default="not_assessed",
        ),
    )
    # Both default to the most modest claim. A row written before the
    # derivation ran must not assert that something was human reviewed or
    # that its evidence graded well.

    # public_grade was VARCHAR(8). The quality contract permits any string,
    # so a longer grade could not be stored at all — and the failure landed
    # on the write *after* a publication's artifact had been promoted. The
    # ordering is narrowed in code; the column should not narrow the value.
    op.alter_column(
        "quality_assessments",
        "public_grade",
        type_=sa.Text(),
        existing_type=sa.String(length=8),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Restore the narrower column, or refuse. Silently truncating a
    # published grade would leave the archive asserting a quality string
    # nobody wrote, which is worse than a failed downgrade.
    too_long = op.get_bind().execute(
        sa.text(
            "SELECT count(*) FROM quality_assessments "
            "WHERE length(public_grade) > 8"
        )
    ).scalar_one()
    if too_long:
        raise RuntimeError(
            f"{too_long} quality grade(s) exceed 8 characters and would be "
            "truncated by this downgrade. Shorten or remove them first; this "
            "migration will not silently rewrite published grades."
        )
    op.alter_column(
        "quality_assessments",
        "public_grade",
        type_=sa.String(length=8),
        existing_type=sa.Text(),
        existing_nullable=False,
    )
    op.drop_column("coverage_entries", "quality_floor")
    op.drop_column("coverage_entries", "review_status")
    op.execute("DROP TYPE quality_floor")
    op.execute("DROP TYPE review_status")
    op.execute(
        "ALTER TYPE publication_tier RENAME VALUE 'enriched' TO 'reviewed_enriched'"
    )
