"""Allow quality assessments to target methodologies explicitly.

Revision ID: 20260724_0006
Revises: 20260723_0005
Create Date: 2026-07-24
"""

from alembic import op

revision = "20260724_0006"
down_revision = "20260723_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE quality_assessments
          DROP CONSTRAINT quality_assessments_check,
          ADD COLUMN target_methodology_id UUID
            REFERENCES methodologies(id) ON DELETE RESTRICT,
          ADD CONSTRAINT quality_assessments_has_target
            CHECK (
              num_nonnulls(
                source_release_id,
                claim_id,
                observation_id,
                derived_value_id,
                target_methodology_id
              ) >= 1
            );
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM quality_assessments
            WHERE target_methodology_id IS NOT NULL
              AND num_nonnulls(
                source_release_id,
                claim_id,
                observation_id,
                derived_value_id
              ) = 0
          ) THEN
            RAISE EXCEPTION
              'cannot downgrade while methodology-only quality assessments exist';
          END IF;
        END;
        $$;

        ALTER TABLE quality_assessments
          DROP CONSTRAINT quality_assessments_has_target,
          DROP COLUMN target_methodology_id,
          ADD CONSTRAINT quality_assessments_check
            CHECK (
              num_nonnulls(
                source_release_id,
                claim_id,
                observation_id,
                derived_value_id
              ) >= 1
            );
        """
    )
