"""Bind the admitted event set to each published version (epic #71, G3b-1).

Featured means emphasized first, not retained alone. A date can legitimately
hold more than one recorded event once a human has adjudicated them distinct,
and the version that publishes them has to remember which events it admitted —
not merely which one it led with.

Inferring the set from statement roots is not sufficient. The derivation can
only recognise an event whose statements root on a relation it knows about
(identity, primary `EventTime` provenance, `EventLocation` provenance), so a
co-published event whose statements do not simply vanishes from the manifest's
apparent contents. That is a collision-safety failure, not a display one: the
guard checks a new candidate against the events behind the date's manifest, so
an event that disappears takes its identity decision with it and a later
candidate could be cleared against a date nobody judged it against.

`featured_selection_id` pins the exact editorial decision the version published
under. Recording only the winning root would let a later decision change what an
immutable artifact is understood to have claimed. `statement_count` records how
much of the section each event contributed, so a successor can regroup retained
statements by event and keep the featured event leading.

Written inside the publication transaction, beside the statement evidence and
before coverage points at the manifest: a binding written afterwards leaves a
window where the date is discoverable with its admitted set missing, and the
guard's fallback under-reports precisely then.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260805_0019"
down_revision = "20260803_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "publication_recorded_events",
        sa.Column(
            "publication_manifest_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("publication_manifests.id", ondelete="RESTRICT"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "event_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("events.id", ondelete="RESTRICT"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "is_featured", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "featured_selection_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("editorial_selections.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("display_order", sa.Integer(), nullable=False),
        # How much of the recorded section each event contributed, so a
        # successor can regroup the retained statements by event rather than
        # guess at boundaries and risk attributing one event's statement to
        # another.
        sa.Column(
            "statement_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "display_order >= 0", name="publication_recorded_event_order"
        ),
        sa.CheckConstraint(
            "statement_count >= 0",
            name="publication_recorded_event_statement_count",
        ),
        # Only the featured row carries the selection it was chosen by.
        sa.CheckConstraint(
            "is_featured OR featured_selection_id IS NULL",
            name="publication_recorded_event_selection_on_featured",
        ),
    )
    # A version has one headline. Enforced in the database because "which event
    # did this artifact lead with" is a claim the archive makes to a reader.
    op.create_index(
        "publication_recorded_events_one_featured",
        "publication_recorded_events",
        ["publication_manifest_id"],
        unique=True,
        postgresql_where=sa.text("is_featured"),
    )
    # The binding describes an immutable artifact, so it is immutable too, using
    # the same function as every other append-only governance record.
    op.execute(
        "CREATE TRIGGER publication_recorded_events_append_only "
        "BEFORE UPDATE OR DELETE ON publication_recorded_events "
        "FOR EACH ROW EXECUTE FUNCTION prevent_governance_record_mutation()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS publication_recorded_events_append_only "
        "ON publication_recorded_events"
    )
    op.drop_index(
        "publication_recorded_events_one_featured",
        table_name="publication_recorded_events",
    )
    op.drop_table("publication_recorded_events")
