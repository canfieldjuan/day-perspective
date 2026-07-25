"""Allow append-only editorial selection transitions.

Revision ID: 20260724_0010
Revises: 20260724_0009
Create Date: 2026-07-24
"""

from alembic import op

revision = "20260724_0010"
down_revision = "20260724_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DROP INDEX editorial_selections_resolved_once;
        DROP INDEX editorial_selections_derived_once;
        ALTER TABLE editorial_selections
          ADD COLUMN decision_version INTEGER NOT NULL DEFAULT 1
          CHECK (decision_version > 0);
        ALTER TABLE editorial_selections
          ALTER COLUMN decision_version DROP DEFAULT;
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


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM editorial_selections
            GROUP BY profile_date, section_key,
              COALESCE(resolved_claim_id, derived_value_id)
            HAVING count(*) > 1
          ) THEN
            RAISE EXCEPTION
              'unsafe downgrade blocked: editorial decision history exists';
          END IF;
        END;
        $$;
        DROP INDEX editorial_selections_derived_history;
        DROP INDEX editorial_selections_resolved_history;
        ALTER TABLE editorial_selections DROP COLUMN decision_version;
        CREATE UNIQUE INDEX editorial_selections_resolved_once
          ON editorial_selections (profile_date, section_key, resolved_claim_id)
          WHERE resolved_claim_id IS NOT NULL;
        CREATE UNIQUE INDEX editorial_selections_derived_once
          ON editorial_selections (profile_date, section_key, derived_value_id)
          WHERE derived_value_id IS NOT NULL;
        """
    )
