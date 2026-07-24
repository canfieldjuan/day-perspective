"""Add the evidence fields required by the USGS vertical slice.

Revision ID: 20260724_0007
Revises: 20260724_0006
Create Date: 2026-07-24
"""

from alembic import op

revision = "20260724_0007"
down_revision = "20260724_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE temporal_precision ADD VALUE IF NOT EXISTS 'second' BEFORE 'day'")
    op.execute(
        """
        CREATE TABLE raw_source_records (
          id UUID PRIMARY KEY,
          source_release_id UUID NOT NULL REFERENCES source_releases(id) ON DELETE RESTRICT,
          source_record_id VARCHAR(240) NOT NULL,
          source_record_locator TEXT NOT NULL,
          raw_storage_uri TEXT NOT NULL,
          raw_checksum_sha256 VARCHAR(64) NOT NULL
            CHECK (raw_checksum_sha256 ~ '^[0-9a-f]{64}$'),
          schema_version VARCHAR(80) NOT NULL,
          payload_json JSONB NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT raw_source_records_release_record_key
            UNIQUE (source_release_id, source_record_id)
        );
        CREATE FUNCTION reject_raw_source_record_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'raw source records are immutable after ingestion';
        END;
        $$;
        CREATE TRIGGER raw_source_records_immutable_update
          BEFORE UPDATE OR DELETE ON raw_source_records
          FOR EACH ROW EXECUTE FUNCTION reject_raw_source_record_mutation();

        ALTER TABLE claims ADD COLUMN source_record_hash_sha256 VARCHAR(64);
        UPDATE claims
        SET source_record_hash_sha256 = source_releases.raw_checksum_sha256
        FROM source_releases
        WHERE claims.source_release_id = source_releases.id;
        ALTER TABLE claims
          ALTER COLUMN source_record_hash_sha256 SET NOT NULL,
          ADD CONSTRAINT claims_source_record_hash_format
            CHECK (source_record_hash_sha256 ~ '^[0-9a-f]{64}$'),
          ADD COLUMN unit TEXT,
          ADD COLUMN lower_bound NUMERIC,
          ADD COLUMN upper_bound NUMERIC,
          ADD CONSTRAINT claims_bounds_order
            CHECK (lower_bound IS NULL OR upper_bound IS NULL OR lower_bound <= upper_bound);
        CREATE FUNCTION populate_claim_record_hash()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.source_record_hash_sha256 IS NULL THEN
            SELECT raw_checksum_sha256 INTO NEW.source_record_hash_sha256
            FROM source_releases WHERE id = NEW.source_release_id;
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER claims_populate_record_hash
          BEFORE INSERT ON claims
          FOR EACH ROW EXECUTE FUNCTION populate_claim_record_hash();

        ALTER TABLE event_times
          ADD COLUMN exact_timestamp TIMESTAMPTZ,
          ADD COLUMN local_date DATE,
          ADD COLUMN timezone_name TEXT,
          ADD COLUMN utc_offset_minutes INTEGER,
          ADD COLUMN interpretation TEXT,
          ADD CONSTRAINT event_times_local_interpretation_complete
            CHECK (
              local_date IS NULL OR (
                exact_timestamp IS NOT NULL AND timezone_name IS NOT NULL
                AND utc_offset_minutes IS NOT NULL AND interpretation IS NOT NULL
              )
            );

        ALTER TABLE quality_assessments
          ADD COLUMN public_grade VARCHAR(8),
          ADD COLUMN public_explanation TEXT,
          ADD CONSTRAINT quality_assessments_public_pair
            CHECK (
              (public_grade IS NULL AND public_explanation IS NULL)
              OR (public_grade IS NOT NULL AND public_explanation IS NOT NULL)
            );
        ALTER TABLE publication_manifests
          ADD COLUMN editorial_revision INTEGER NOT NULL DEFAULT 1
            CHECK (editorial_revision > 0);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE publication_manifests DROP COLUMN editorial_revision;
        ALTER TABLE quality_assessments
          DROP CONSTRAINT quality_assessments_public_pair,
          DROP COLUMN public_explanation,
          DROP COLUMN public_grade;
        ALTER TABLE event_times
          DROP CONSTRAINT event_times_local_interpretation_complete,
          DROP COLUMN interpretation,
          DROP COLUMN utc_offset_minutes,
          DROP COLUMN timezone_name,
          DROP COLUMN local_date,
          DROP COLUMN exact_timestamp;
        DROP TRIGGER claims_populate_record_hash ON claims;
        ALTER TABLE claims
          DROP CONSTRAINT claims_bounds_order,
          DROP COLUMN upper_bound,
          DROP COLUMN lower_bound,
          DROP COLUMN unit,
          DROP CONSTRAINT claims_source_record_hash_format,
          DROP COLUMN source_record_hash_sha256;
        DROP FUNCTION populate_claim_record_hash();
        DROP TRIGGER raw_source_records_immutable_update ON raw_source_records;
        DROP FUNCTION reject_raw_source_record_mutation();
        DROP TABLE raw_source_records;
        """
    )
