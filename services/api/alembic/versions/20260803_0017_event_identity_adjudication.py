"""Durable event-identity adjudication (epic #71, G3a).

A recorded-event collision opened a review task asking a human to choose merge,
supersede, or distinct-event, and had nowhere to put the answer. The collision
guard could not read "these two are distinct", so closing the task changed
nothing: the next publish attempt collided and deferred again.

This table is that answer. It identifies canonical events rather than publication
manifests, because a manifest is a versioned artifact — a decision keyed on one
would stop applying the moment the date was republished, which is precisely when
it still needs to hold.

The pair is stored canonically ordered (`event_a_id < event_b_id`), so the
unordered pair is unique and a self-pair is rejected by the same constraint.
History is append-only: a changed decision adds a version that supersedes the
previous one, and every foreign key is RESTRICT so the audit trail behind a
published decision cannot be deleted.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260803_0017"
down_revision = "20260727_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_identity_adjudications",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "event_a_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("events.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "event_b_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("events.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("profile_date", sa.Date(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column(
            "survivor_event_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("events.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("decision_version", sa.Integer(), nullable=False),
        sa.Column(
            "supersedes_adjudication_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("event_identity_adjudications.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("reviewer", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column(
            "review_task_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("review_tasks.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # Canonical ordering and the self-pair rejection are the same rule: a
        # pair is unordered, and an event is not a pair with itself.
        sa.CheckConstraint(
            "event_a_id < event_b_id",
            name="event_identity_adjudication_canonical_pair",
        ),
        sa.CheckConstraint(
            "decision IN ('distinct_event','merge','supersede','deferred')",
            name="event_identity_adjudication_decision",
        ),
        # merge and supersede name a survivor; distinct_event and deferred leave
        # both events standing, so a survivor on either is a contradiction.
        sa.CheckConstraint(
            "(decision IN ('merge','supersede')) = (survivor_event_id IS NOT NULL)",
            name="event_identity_adjudication_survivor_required",
        ),
        sa.CheckConstraint(
            "survivor_event_id IS NULL "
            "OR survivor_event_id IN (event_a_id, event_b_id)",
            name="event_identity_adjudication_survivor_in_pair",
        ),
        # A blank reviewer is not a person, and this record's whole value is that
        # a person decided.
        sa.CheckConstraint(
            "btrim(reviewer) <> ''",
            name="event_identity_adjudication_reviewer_present",
        ),
        sa.CheckConstraint(
            "decision_version >= 1",
            name="event_identity_adjudication_version",
        ),
    )
    op.create_index(
        "event_identity_adjudication_history",
        "event_identity_adjudications",
        ["event_a_id", "event_b_id", "decision_version"],
        unique=True,
    )
    # Append-only in the database, not merely in the writer. The unique history
    # index stops two rows sharing a (pair, version); it does nothing about an
    # UPDATE that rewrites the latest decision or reviewer in place, or a DELETE
    # that removes it — and both would silently change what the publication
    # guard consumes. `prevent_governance_record_mutation` is the same function
    # already guarding source_release_licenses, claim_review_decisions and
    # editorial_selections (migration 0008); a governance record that this one
    # did not use would be the only mutable one.
    op.execute(
        "CREATE TRIGGER event_identity_adjudications_append_only "
        "BEFORE UPDATE OR DELETE ON event_identity_adjudications "
        "FOR EACH ROW EXECUTE FUNCTION prevent_governance_record_mutation()"
    )

    # A merge-review task asks a human about one specific collision. Until now
    # that subject existed only inside the task's rationale prose, so if the
    # date were republished while the task waited, the answer would be recorded
    # against whatever the coverage index pointed at by then — a durable
    # identity decision about events the reviewer never evaluated.
    op.add_column(
        "review_tasks",
        sa.Column(
            "context_manifest_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("publication_manifests.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("review_tasks", "context_manifest_id")
    op.execute(
        "DROP TRIGGER IF EXISTS event_identity_adjudications_append_only "
        "ON event_identity_adjudications"
    )
    op.drop_index(
        "event_identity_adjudication_history",
        table_name="event_identity_adjudications",
    )
    op.drop_table("event_identity_adjudications")
