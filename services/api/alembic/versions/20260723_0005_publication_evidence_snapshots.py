"""Capture immutable evidence snapshots for published statements.

Revision ID: 20260723_0005
Revises: 20260723_0004
Create Date: 2026-07-23
"""

from alembic import op

revision = "20260723_0005"
down_revision = "20260723_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM publication_statement_evidence) THEN
            RAISE EXCEPTION
              'publication evidence rows require forensic snapshot migration before 20260723_0005';
          END IF;
        END;
        $$;

        ALTER TABLE publication_statement_evidence
          ADD COLUMN evidence_snapshot JSONB NOT NULL,
          ADD COLUMN evidence_snapshot_hash VARCHAR(64) NOT NULL,
          ADD CONSTRAINT publication_statement_evidence_snapshot_object
            CHECK (jsonb_typeof(evidence_snapshot) = 'object'),
          ADD CONSTRAINT publication_statement_evidence_snapshot_hash_format
            CHECK (evidence_snapshot_hash ~ '^[0-9a-f]{64}$');
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE publication_statement_evidence
          DROP CONSTRAINT IF EXISTS publication_statement_evidence_snapshot_hash_format,
          DROP CONSTRAINT IF EXISTS publication_statement_evidence_snapshot_object,
          DROP COLUMN IF EXISTS evidence_snapshot_hash,
          DROP COLUMN IF EXISTS evidence_snapshot;
        """
    )
