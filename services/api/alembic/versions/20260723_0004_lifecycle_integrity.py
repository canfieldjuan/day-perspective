"""Centralize lifecycle cardinality and successor invariants.

Revision ID: 20260723_0004
Revises: 20260723_0003
Create Date: 2026-07-23
"""

from alembic import op

revision = "20260723_0004"
down_revision = "20260723_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS resolved_claim_requires_evidence ON resolved_claims;
        DROP TRIGGER IF EXISTS resolved_claim_evidence_cardinality ON resolved_claim_evidence;
        DROP FUNCTION IF EXISTS require_resolved_claim_evidence();

        CREATE FUNCTION assert_resolved_claim_has_supporting_evidence(target_resolved_claim_id UUID) RETURNS void AS $$
        BEGIN
          IF EXISTS (SELECT 1 FROM resolved_claims WHERE id = target_resolved_claim_id)
            AND NOT EXISTS (
              SELECT 1 FROM resolved_claim_evidence
              WHERE resolved_claim_id = target_resolved_claim_id AND stance = 'supporting'
            ) THEN
            RAISE EXCEPTION 'resolved claims require at least one supporting imported claim';
          END IF;
        END;
        $$ LANGUAGE plpgsql;

        CREATE FUNCTION validate_resolved_claim_write() RETURNS trigger AS $$
        BEGIN
          PERFORM assert_resolved_claim_has_supporting_evidence(NEW.id);
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;

        CREATE FUNCTION validate_resolved_claim_evidence_write() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'INSERT' THEN
            PERFORM assert_resolved_claim_has_supporting_evidence(NEW.resolved_claim_id);
          ELSIF TG_OP = 'DELETE' THEN
            PERFORM assert_resolved_claim_has_supporting_evidence(OLD.resolved_claim_id);
          ELSE
            PERFORM assert_resolved_claim_has_supporting_evidence(OLD.resolved_claim_id);
            IF NEW.resolved_claim_id IS DISTINCT FROM OLD.resolved_claim_id THEN
              PERFORM assert_resolved_claim_has_supporting_evidence(NEW.resolved_claim_id);
            END IF;
          END IF;
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;

        CREATE CONSTRAINT TRIGGER resolved_claim_requires_evidence
          AFTER INSERT OR UPDATE ON resolved_claims
          DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION validate_resolved_claim_write();
        CREATE CONSTRAINT TRIGGER resolved_claim_evidence_cardinality
          AFTER INSERT OR UPDATE OR DELETE ON resolved_claim_evidence
          DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION validate_resolved_claim_evidence_write();

        DROP TRIGGER IF EXISTS derived_value_requires_lineage ON derived_values;
        DROP TRIGGER IF EXISTS derived_value_input_requires_lineage ON derived_value_inputs;
        DROP FUNCTION IF EXISTS require_derived_value_lineage();

        CREATE FUNCTION assert_derived_value_has_lineage(target_derived_value_id UUID) RETURNS void AS $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM derived_values
            WHERE id = target_derived_value_id
              AND provenance_resolved_claim_id IS NULL
          ) AND NOT EXISTS (
            SELECT 1 FROM derived_value_inputs WHERE derived_value_id = target_derived_value_id
          ) THEN
            RAISE EXCEPTION 'derived values require a resolved-claim provenance link or input lineage';
          END IF;
        END;
        $$ LANGUAGE plpgsql;

        CREATE FUNCTION validate_derived_value_write() RETURNS trigger AS $$
        BEGIN
          PERFORM assert_derived_value_has_lineage(NEW.id);
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;

        CREATE FUNCTION validate_derived_value_input_write() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'INSERT' THEN
            PERFORM assert_derived_value_has_lineage(NEW.derived_value_id);
          ELSIF TG_OP = 'DELETE' THEN
            PERFORM assert_derived_value_has_lineage(OLD.derived_value_id);
          ELSE
            PERFORM assert_derived_value_has_lineage(OLD.derived_value_id);
            IF NEW.derived_value_id IS DISTINCT FROM OLD.derived_value_id THEN
              PERFORM assert_derived_value_has_lineage(NEW.derived_value_id);
            END IF;
          END IF;
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;

        CREATE CONSTRAINT TRIGGER derived_value_requires_lineage
          AFTER INSERT OR UPDATE ON derived_values
          DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION validate_derived_value_write();
        CREATE CONSTRAINT TRIGGER derived_value_input_requires_lineage
          AFTER INSERT OR UPDATE OR DELETE ON derived_value_inputs
          DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION validate_derived_value_input_write();

        ALTER TABLE publication_manifests
          ADD CONSTRAINT publication_manifests_single_successor UNIQUE (supersedes_manifest_id);
        ALTER TABLE day_profiles
          ADD CONSTRAINT day_profiles_single_successor UNIQUE (supersedes_day_profile_id);

        CREATE FUNCTION validate_publication_manifest_supersession() RETURNS trigger AS $$
        DECLARE predecessor_date DATE;
        DECLARE predecessor_type profile_type;
        DECLARE predecessor_status publication_status;
        BEGIN
          IF NEW.supersedes_manifest_id IS NULL THEN
            RETURN NEW;
          END IF;
          SELECT profile_date, profile_type, status
          INTO predecessor_date, predecessor_type, predecessor_status
          FROM publication_manifests WHERE id = NEW.supersedes_manifest_id;
          IF NOT FOUND OR predecessor_status <> 'published' THEN
            RAISE EXCEPTION 'manifest supersession requires a published predecessor';
          END IF;
          IF predecessor_date <> NEW.profile_date OR predecessor_type <> NEW.profile_type THEN
            RAISE EXCEPTION 'manifest supersession must retain the same date and profile type';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER publication_manifests_validate_supersession
          BEFORE INSERT OR UPDATE ON publication_manifests
          FOR EACH ROW EXECUTE FUNCTION validate_publication_manifest_supersession();

        CREATE OR REPLACE FUNCTION validate_day_profile_manifest() RETURNS trigger AS $$
        DECLARE manifest_date DATE;
        DECLARE manifest_type profile_type;
        DECLARE manifest_status publication_status;
        DECLARE manifest_hash VARCHAR(64);
        DECLARE manifest_predecessor_id UUID;
        DECLARE predecessor_manifest_id UUID;
        DECLARE predecessor_date DATE;
        DECLARE predecessor_type profile_type;
        BEGIN
          SELECT profile_date, profile_type, status, content_hash, supersedes_manifest_id
          INTO manifest_date, manifest_type, manifest_status, manifest_hash, manifest_predecessor_id
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
          IF NEW.supersedes_day_profile_id IS NULL AND manifest_predecessor_id IS NOT NULL THEN
            RAISE EXCEPTION 'profile supersession requires a predecessor day profile';
          END IF;
          IF NEW.supersedes_day_profile_id IS NOT NULL THEN
            SELECT publication_manifest_id, profile_date, profile_type
            INTO predecessor_manifest_id, predecessor_date, predecessor_type
            FROM day_profiles WHERE id = NEW.supersedes_day_profile_id;
            IF NOT FOUND
              OR predecessor_date <> NEW.profile_date
              OR predecessor_type <> NEW.profile_type
              OR predecessor_manifest_id IS DISTINCT FROM manifest_predecessor_id THEN
              RAISE EXCEPTION 'profile supersession must match the manifest predecessor and identity';
            END IF;
          END IF;
          IF TG_OP = 'UPDATE' THEN
            RAISE EXCEPTION 'day profiles attached to published manifests are immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS publication_manifests_validate_supersession ON publication_manifests;
        DROP FUNCTION IF EXISTS validate_publication_manifest_supersession();
        ALTER TABLE day_profiles DROP CONSTRAINT IF EXISTS day_profiles_single_successor;
        ALTER TABLE publication_manifests DROP CONSTRAINT IF EXISTS publication_manifests_single_successor;

        DROP TRIGGER IF EXISTS resolved_claim_requires_evidence ON resolved_claims;
        DROP TRIGGER IF EXISTS resolved_claim_evidence_cardinality ON resolved_claim_evidence;
        DROP FUNCTION IF EXISTS validate_resolved_claim_evidence_write();
        DROP FUNCTION IF EXISTS validate_resolved_claim_write();
        DROP FUNCTION IF EXISTS assert_resolved_claim_has_supporting_evidence(UUID);
        CREATE FUNCTION require_resolved_claim_evidence() RETURNS trigger AS $$
        DECLARE target_resolved_claim_id UUID;
        BEGIN
          IF TG_TABLE_NAME = 'resolved_claims' THEN
            target_resolved_claim_id := NEW.id;
          ELSIF TG_OP = 'DELETE' THEN
            target_resolved_claim_id := OLD.resolved_claim_id;
          ELSE
            target_resolved_claim_id := NEW.resolved_claim_id;
          END IF;
          IF EXISTS (SELECT 1 FROM resolved_claims WHERE id = target_resolved_claim_id)
            AND NOT EXISTS (
              SELECT 1 FROM resolved_claim_evidence WHERE resolved_claim_id = target_resolved_claim_id
            ) THEN
            RAISE EXCEPTION 'resolved claims require at least one supporting or dissenting imported claim';
          END IF;
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        CREATE CONSTRAINT TRIGGER resolved_claim_requires_evidence
          AFTER INSERT OR UPDATE ON resolved_claims
          DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION require_resolved_claim_evidence();
        CREATE CONSTRAINT TRIGGER resolved_claim_evidence_cardinality
          AFTER INSERT OR UPDATE OR DELETE ON resolved_claim_evidence
          DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION require_resolved_claim_evidence();

        DROP TRIGGER IF EXISTS derived_value_requires_lineage ON derived_values;
        DROP TRIGGER IF EXISTS derived_value_input_requires_lineage ON derived_value_inputs;
        DROP FUNCTION IF EXISTS validate_derived_value_input_write();
        DROP FUNCTION IF EXISTS validate_derived_value_write();
        DROP FUNCTION IF EXISTS assert_derived_value_has_lineage(UUID);
        CREATE FUNCTION require_derived_value_lineage() RETURNS trigger AS $$
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
        """
    )
