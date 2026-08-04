"""Admit the featured-event decision namespace (epic #71, G3a part 2).

Which of a date's events is featured is an editorial decision about that date,
so it is recorded in `editorial_selections` rather than a parallel table — one
writer, one history, no second implementation that could disagree. That was the
structural defect MD1 (#45) was cut for after seven review rounds.

`featured_event` is a decision namespace, not a rendered section: no published
payload carries a `featured_event` section, and the published-section
vocabularies elsewhere (the coverage index's section counts) are deliberately
left alone.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260803_0018"
down_revision = "20260803_0017"
branch_labels = None
depends_on = None

_PUBLISHED_SECTIONS = (
    "recorded_on_this_date",
    "typical_day_in_this_year",
    "wider_historical_context",
    "curated_claims",
    "derived_comparisons",
    "wonder_and_progress",
    "evidence_notes",
)


def _set_vocabulary(sections: tuple[str, ...]) -> None:
    values = ",".join(f"'{section}'" for section in sections)
    op.execute(
        "ALTER TABLE editorial_selections "
        "DROP CONSTRAINT editorial_selections_section_key_check"
    )
    op.execute(
        "ALTER TABLE editorial_selections ADD CONSTRAINT "
        f"editorial_selections_section_key_check CHECK (section_key IN ({values}))"
    )


def upgrade() -> None:
    _set_vocabulary((*_PUBLISHED_SECTIONS, "featured_event"))


def downgrade() -> None:
    # Refuse rather than silently drop featured-event decisions: the constraint
    # cannot be narrowed while rows depend on the wider vocabulary, and deleting
    # a human's recorded editorial decision to make a downgrade succeed is worse
    # than a failed downgrade.
    remaining = op.get_bind().execute(
        sa.text(
            "SELECT count(*) FROM editorial_selections "
            "WHERE section_key = 'featured_event'"
        )
    ).scalar_one()
    if remaining:
        raise RuntimeError(
            f"{remaining} featured-event editorial decision(s) exist and would be "
            "orphaned by narrowing the section vocabulary. Remove them "
            "deliberately first; this migration will not delete recorded "
            "editorial decisions."
        )
    _set_vocabulary(_PUBLISHED_SECTIONS)
