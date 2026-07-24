"""Harden publication, derivation, coverage, and correction integrity.

Revision ID: 20260723_0003
Revises: 20260723_0002
Create Date: 2026-07-23
"""

from alembic import op

revision = "20260723_0003"
down_revision = "20260723_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE metric_coverage ALTER COLUMN coverage_fraction DROP NOT NULL;
        ALTER TABLE metric_coverage
          ADD CONSTRAINT metric_coverage_value_presence
          CHECK (
            (data_status = 'missing' AND coverage_fraction IS NULL AND missing_reason IS NOT NULL)
            OR
            (data_status <> 'missing' AND coverage_fraction IS NOT NULL AND missing_reason IS NULL)
          );

        CREATE OR REPLACE FUNCTION prevent_final_publication_statement_evidence_mutation() RETURNS trigger AS $$
        DECLARE old_manifest_status publication_status;
        DECLARE new_manifest_status publication_status;
        BEGIN
          IF TG_OP <> 'INSERT' THEN
            SELECT status INTO old_manifest_status
            FROM publication_manifests WHERE id = OLD.publication_manifest_id;
            IF old_manifest_status IN ('published','superseded','withdrawn') THEN
              RAISE EXCEPTION 'publication statement evidence is immutable after publication';
            END IF;
          END IF;
          IF TG_OP <> 'DELETE' THEN
            SELECT status INTO new_manifest_status
            FROM publication_manifests WHERE id = NEW.publication_manifest_id;
            IF new_manifest_status IN ('published','superseded','withdrawn') THEN
              RAISE EXCEPTION 'publication statement evidence is immutable after publication';
            END IF;
          END IF;
          IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE OR REPLACE FUNCTION require_derived_value_lineage() RETURNS trigger AS $$
        DECLARE target_derived_value_id UUID;
        BEGIN
          IF TG_TABLE_NAME = 'derived_values' THEN
            target_derived_value_id := NEW.id;
          ELSIF TG_OP = 'DELETE' THEN
            target_derived_value_id := OLD.derived_value_id;
          ELSE
            target_derived_value_id := NEW.derived_value_id;
          END IF;
          IF EXISTS (
            SELECT 1 FROM derived_values
            WHERE id = target_derived_value_id
              AND provenance_resolved_claim_id IS NULL
          ) AND NOT EXISTS (
            SELECT 1 FROM derived_value_inputs
            WHERE derived_value_id = target_derived_value_id
          ) THEN
            RAISE EXCEPTION 'derived values require a resolved-claim provenance link or input lineage';
          END IF;
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        CREATE CONSTRAINT TRIGGER derived_value_requires_lineage
          AFTER INSERT OR UPDATE ON derived_values
          DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION require_derived_value_lineage();
        CREATE CONSTRAINT TRIGGER derived_value_input_requires_lineage
          AFTER INSERT OR UPDATE OR DELETE ON derived_value_inputs
          DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION require_derived_value_lineage();

        CREATE OR REPLACE FUNCTION validate_day_profile_manifest() RETURNS trigger AS $$
        DECLARE manifest_date DATE;
        DECLARE manifest_type profile_type;
        DECLARE manifest_status publication_status;
        DECLARE manifest_hash VARCHAR(64);
        BEGIN
          SELECT profile_date, profile_type, status, content_hash
          INTO manifest_date, manifest_type, manifest_status, manifest_hash
          FROM publication_manifests WHERE id = NEW.publication_manifest_id;
          IF NOT FOUND OR manifest_status <> 'published' THEN
            RAISE EXCEPTION 'day profile requires a published manifest';
          END IF;
          IF manifest_date <> NEW.profile_date OR manifest_type <> NEW.profile_type THEN
            RAISE EXCEPTION 'day profile date and type must match its manifest';
          END IF;
          IF manifest_hash <> NEW.content_hash THEN
            RAISE EXCEPTION 'day profile content hash must match its manifest';
          END IF;
          IF TG_OP = 'UPDATE' THEN
            RAISE EXCEPTION 'day profiles attached to published manifests are immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE OR REPLACE FUNCTION validate_correction_replacement() RETURNS trigger AS $$
        DECLARE original_date DATE;
        DECLARE replacement_date DATE;
        DECLARE original_type profile_type;
        DECLARE replacement_type profile_type;
        DECLARE parent_manifest UUID;
        DECLARE replacement_status publication_status;
        DECLARE original_status publication_status;
        DECLARE original_profile_id UUID;
        DECLARE replacement_predecessor_id UUID;
        BEGIN
          SELECT profile_date, profile_type, status
          INTO original_date, original_type, original_status
          FROM publication_manifests WHERE id = NEW.original_manifest_id;
          IF NOT FOUND OR original_status <> 'published' THEN
            RAISE EXCEPTION 'correction original must be published';
          END IF;
          SELECT profile_date, profile_type, supersedes_manifest_id, status
          INTO replacement_date, replacement_type, parent_manifest, replacement_status
          FROM publication_manifests WHERE id = NEW.replacement_manifest_id;
          IF NOT FOUND OR parent_manifest <> NEW.original_manifest_id OR replacement_status <> 'published' THEN
            RAISE EXCEPTION 'correction replacement must be published and supersede the original';
          END IF;
          IF original_date <> replacement_date OR original_type <> replacement_type THEN
            RAISE EXCEPTION 'correction replacement must have the original date and profile type';
          END IF;
          SELECT id INTO original_profile_id
          FROM day_profiles WHERE publication_manifest_id = NEW.original_manifest_id;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'correction original must have a published day profile';
          END IF;
          SELECT supersedes_day_profile_id INTO replacement_predecessor_id
          FROM day_profiles WHERE publication_manifest_id = NEW.replacement_manifest_id;
          IF NOT FOUND OR replacement_predecessor_id IS DISTINCT FROM original_profile_id THEN
            RAISE EXCEPTION 'correction replacement profile must supersede the original profile';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS derived_value_input_requires_lineage ON derived_value_inputs;
        DROP TRIGGER IF EXISTS derived_value_requires_lineage ON derived_values;
        DROP FUNCTION IF EXISTS require_derived_value_lineage();

        CREATE OR REPLACE FUNCTION prevent_final_publication_statement_evidence_mutation() RETURNS trigger AS $$
        DECLARE manifest_status publication_status;
        DECLARE target_manifest_id UUID;
        BEGIN
          target_manifest_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.publication_manifest_id ELSE NEW.publication_manifest_id END;
          SELECT status INTO manifest_status FROM publication_manifests WHERE id = target_manifest_id;
          IF manifest_status IN ('published','superseded','withdrawn') THEN
            RAISE EXCEPTION 'publication statement evidence is immutable after publication';
          END IF;
          IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE OR REPLACE FUNCTION validate_day_profile_manifest() RETURNS trigger AS $$
        DECLARE manifest_date DATE; DECLARE manifest_type profile_type; DECLARE manifest_status publication_status;
        BEGIN
          SELECT profile_date, profile_type, status INTO manifest_date, manifest_type, manifest_status
          FROM publication_manifests WHERE id = NEW.publication_manifest_id;
          IF NOT FOUND OR manifest_status <> 'published' THEN
            RAISE EXCEPTION 'day profile requires a published manifest';
          END IF;
          IF manifest_date <> NEW.profile_date OR manifest_type <> NEW.profile_type THEN
            RAISE EXCEPTION 'day profile date and type must match its manifest';
          END IF;
          IF TG_OP = 'UPDATE' THEN
            RAISE EXCEPTION 'day profiles attached to published manifests are immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE OR REPLACE FUNCTION validate_correction_replacement() RETURNS trigger AS $$
        DECLARE parent_manifest UUID;
        DECLARE replacement_status publication_status;
        DECLARE original_status publication_status;
        BEGIN
          SELECT status INTO original_status FROM publication_manifests WHERE id = NEW.original_manifest_id;
          IF NOT FOUND OR original_status <> 'published' THEN
            RAISE EXCEPTION 'correction original must be published';
          END IF;
          SELECT supersedes_manifest_id, status INTO parent_manifest, replacement_status
          FROM publication_manifests WHERE id = NEW.replacement_manifest_id;
          IF NOT FOUND OR parent_manifest <> NEW.original_manifest_id OR replacement_status <> 'published' THEN
            RAISE EXCEPTION 'correction replacement must be published and supersede the original';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        ALTER TABLE metric_coverage DROP CONSTRAINT IF EXISTS metric_coverage_value_presence;
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM metric_coverage WHERE coverage_fraction IS NULL) THEN
            RAISE EXCEPTION 'cannot downgrade while metric coverage has explicit missing values';
          END IF;
        END;
        $$;
        ALTER TABLE metric_coverage ALTER COLUMN coverage_fraction SET NOT NULL;
        """
    )
