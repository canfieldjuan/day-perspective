"""Add immutable licensing, review decisions, and editorial selections.

Revision ID: 20260724_0008_governance
Revises: 20260724_0008
Create Date: 2026-07-24
"""

from alembic import op

revision = "20260724_0008_governance"
down_revision = "20260724_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE source_release_licenses (
          source_release_id UUID PRIMARY KEY
            REFERENCES source_releases(id) ON DELETE RESTRICT,
          license_identifier VARCHAR(160) NOT NULL,
          license_snapshot TEXT NOT NULL,
          license_snapshot_hash VARCHAR(64) NOT NULL
            CHECK (license_snapshot_hash ~ '^[0-9a-f]{64}$'),
          terms_url TEXT NOT NULL,
          commercial_use_permission BOOLEAN,
          redistribution_permission BOOLEAN,
          derivatives_permission BOOLEAN,
          attribution_required BOOLEAN,
          attribution_text TEXT,
          public_display_permission BOOLEAN,
          raw_download_permission BOOLEAN,
          terms_checked_at DATE NOT NULL,
          legal_review_status legal_review_status NOT NULL DEFAULT 'pending',
          created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CHECK (attribution_required IS NOT TRUE OR attribution_text IS NOT NULL)
        );

        CREATE TABLE claim_review_decisions (
          id UUID PRIMARY KEY,
          claim_id UUID NOT NULL REFERENCES claims(id) ON DELETE RESTRICT,
          decision VARCHAR(16) NOT NULL
            CHECK (decision IN ('accepted','rejected','deferred')),
          prior_status claim_assertion_status NOT NULL,
          resulting_status claim_assertion_status NOT NULL,
          rationale TEXT NOT NULL,
          reviewed_by TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE editorial_selections (
          id UUID PRIMARY KEY,
          profile_date DATE NOT NULL
            CHECK (profile_date BETWEEN DATE '1900-01-01' AND DATE '2025-12-31'),
          section_key VARCHAR(80) NOT NULL CHECK (
            section_key IN (
              'recorded_on_this_date',
              'typical_day_in_this_year',
              'wider_historical_context',
              'curated_claims',
              'derived_comparisons',
              'wonder_and_progress',
              'evidence_notes'
            )
          ),
          resolved_claim_id UUID REFERENCES resolved_claims(id) ON DELETE RESTRICT,
          derived_value_id UUID REFERENCES derived_values(id) ON DELETE RESTRICT,
          status VARCHAR(16) NOT NULL
            CHECK (status IN ('selected','rejected','deferred')),
          display_rank INTEGER CHECK (display_rank IS NULL OR display_rank > 0),
          rationale TEXT NOT NULL,
          reviewed_by TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CHECK (num_nonnulls(resolved_claim_id, derived_value_id) = 1)
        );
        CREATE UNIQUE INDEX editorial_selections_resolved_once
          ON editorial_selections (profile_date, section_key, resolved_claim_id)
          WHERE resolved_claim_id IS NOT NULL;
        CREATE UNIQUE INDEX editorial_selections_derived_once
          ON editorial_selections (profile_date, section_key, derived_value_id)
          WHERE derived_value_id IS NOT NULL;

        CREATE FUNCTION prevent_governance_record_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION '% records are append-only', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER source_release_licenses_append_only
          BEFORE UPDATE OR DELETE ON source_release_licenses
          FOR EACH ROW EXECUTE FUNCTION prevent_governance_record_mutation();
        CREATE TRIGGER claim_review_decisions_append_only
          BEFORE UPDATE OR DELETE ON claim_review_decisions
          FOR EACH ROW EXECUTE FUNCTION prevent_governance_record_mutation();
        CREATE TRIGGER editorial_selections_append_only
          BEFORE UPDATE OR DELETE ON editorial_selections
          FOR EACH ROW EXECUTE FUNCTION prevent_governance_record_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS editorial_selections_append_only ON editorial_selections;
        DROP TRIGGER IF EXISTS claim_review_decisions_append_only ON claim_review_decisions;
        DROP TRIGGER IF EXISTS source_release_licenses_append_only ON source_release_licenses;
        DROP TABLE IF EXISTS editorial_selections;
        DROP TABLE IF EXISTS claim_review_decisions;
        DROP TABLE IF EXISTS source_release_licenses;
        DROP FUNCTION IF EXISTS prevent_governance_record_mutation();
        """
    )
