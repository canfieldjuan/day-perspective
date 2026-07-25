"""Retain separate provenance for event local civil-date assignments.

Revision ID: 20260724_0008
Revises: 20260724_0007
Create Date: 2026-07-24
"""

from alembic import op

revision = "20260724_0008"
down_revision = "20260724_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE event_times
          ADD COLUMN local_date_provenance_resolved_claim_id UUID
            REFERENCES resolved_claims(id) ON DELETE RESTRICT;
        ALTER TABLE event_times
          DROP CONSTRAINT event_times_local_interpretation_complete,
          ADD CONSTRAINT event_times_local_interpretation_complete
            CHECK (
              local_date IS NULL OR (
                exact_timestamp IS NOT NULL AND timezone_name IS NOT NULL
                AND utc_offset_minutes IS NOT NULL AND interpretation IS NOT NULL
                AND local_date_provenance_resolved_claim_id IS NOT NULL
              )
            );
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE event_times
          DROP CONSTRAINT event_times_local_interpretation_complete,
          ADD CONSTRAINT event_times_local_interpretation_complete
            CHECK (
              local_date IS NULL OR (
                exact_timestamp IS NOT NULL AND timezone_name IS NOT NULL
                AND utc_offset_minutes IS NOT NULL AND interpretation IS NOT NULL
              )
            ),
          DROP COLUMN local_date_provenance_resolved_claim_id;
        """
    )
