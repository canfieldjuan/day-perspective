"""Let a derivation record another derivation as its input.

`derived_value_inputs` could name an observation or a resolved claim, which
covered every derivation the archive had: each one computed a value from
source records. The first app-derived comparison (epic #51, UC4) computes a
value from *other derived values* — a year's conflict count against the
median of eighty such counts — and the table had no way to say so.

Without this the comparison's stored lineage would name a single conflict
record as the provenance of a median taken over the whole period, which
`docs/PRODUCT_CONTRACT.md` requires to be inspectable. The cohort hash
proves the computation is reproducible; it does not let a reader walk the
inputs.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260727_0015"
down_revision = "20260726_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "derived_value_inputs",
        sa.Column(
            "input_derived_value_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "derived_value_inputs_input_derived_value_id_fkey",
        "derived_value_inputs",
        "derived_values",
        ["input_derived_value_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "derived_value_inputs_input_derived_value_idx",
        "derived_value_inputs",
        ["input_derived_value_id"],
    )
    # The existing check counted only the two original columns, so a row
    # naming a derived input satisfied neither it nor any replacement. It is
    # superseded rather than supplemented: leaving both would reject every
    # row the new column exists to allow.
    op.drop_constraint("derived_value_inputs_check", "derived_value_inputs", type_="check")
    # Still exactly one input kind per row. Without this a row could name
    # none of them and still satisfy the schema, which is how a lineage
    # table stops recording lineage.
    op.create_check_constraint(
        "derived_value_inputs_single_source",
        "derived_value_inputs",
        "num_nonnulls(observation_id, resolved_claim_id, input_derived_value_id) = 1",
    )


def downgrade() -> None:
    op.drop_constraint(
        "derived_value_inputs_single_source",
        "derived_value_inputs",
        type_="check",
    )
    op.create_check_constraint(
        "derived_value_inputs_check",
        "derived_value_inputs",
        "num_nonnulls(observation_id, resolved_claim_id) = 1",
    )
    op.drop_index("derived_value_inputs_input_derived_value_idx")
    op.drop_constraint(
        "derived_value_inputs_input_derived_value_id_fkey",
        "derived_value_inputs",
        type_="foreignkey",
    )
    op.drop_column("derived_value_inputs", "input_derived_value_id")
