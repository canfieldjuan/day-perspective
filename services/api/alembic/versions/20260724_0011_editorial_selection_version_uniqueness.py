"""Enforce one editorial decision per root version.

Revision ID: 20260724_0011
Revises: 20260724_0010
Create Date: 2026-07-24
"""

from alembic import op

revision = "20260724_0011"
down_revision = "20260724_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DROP INDEX editorial_selections_resolved_history;
        DROP INDEX editorial_selections_derived_history;
        CREATE UNIQUE INDEX editorial_selections_resolved_history
          ON editorial_selections (
            profile_date, section_key, resolved_claim_id, decision_version
          ) WHERE resolved_claim_id IS NOT NULL;
        CREATE UNIQUE INDEX editorial_selections_derived_history
          ON editorial_selections (
            profile_date, section_key, derived_value_id, decision_version
          ) WHERE derived_value_id IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX editorial_selections_derived_history;
        DROP INDEX editorial_selections_resolved_history;
        CREATE INDEX editorial_selections_resolved_history
          ON editorial_selections (
            profile_date, section_key, resolved_claim_id, decision_version DESC
          ) WHERE resolved_claim_id IS NOT NULL;
        CREATE INDEX editorial_selections_derived_history
          ON editorial_selections (
            profile_date, section_key, derived_value_id, decision_version DESC
          ) WHERE derived_value_id IS NOT NULL;
        """
    )
