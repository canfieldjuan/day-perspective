"""Enforce publication-statement provenance and lifecycle guards.

Revision ID: 20260723_0002
Revises: 20260723_0001
Create Date: 2026-07-23
"""

from alembic import op

revision = "20260723_0002"
down_revision = "20260723_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE publication_statement_evidence (
          id UUID PRIMARY KEY,
          publication_manifest_id UUID NOT NULL REFERENCES publication_manifests(id) ON DELETE RESTRICT,
          statement_path TEXT NOT NULL,
          resolved_claim_id UUID REFERENCES resolved_claims(id) ON DELETE RESTRICT,
          derived_value_id UUID REFERENCES derived_values(id) ON DELETE RESTRICT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CHECK (num_nonnulls(resolved_claim_id, derived_value_id) = 1),
          UNIQUE (publication_manifest_id, statement_path)
        );

        CREATE FUNCTION prevent_final_publication_statement_evidence_mutation() RETURNS trigger AS $$
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
        CREATE TRIGGER publication_statement_evidence_final_immutable
          BEFORE INSERT OR UPDATE OR DELETE ON publication_statement_evidence
          FOR EACH ROW EXECUTE FUNCTION prevent_final_publication_statement_evidence_mutation();

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
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS resolved_claim_evidence_cardinality ON resolved_claim_evidence;
        DROP TRIGGER IF EXISTS resolved_claim_requires_evidence ON resolved_claims;
        DROP FUNCTION IF EXISTS require_resolved_claim_evidence();
        DROP TRIGGER IF EXISTS publication_statement_evidence_final_immutable ON publication_statement_evidence;
        DROP FUNCTION IF EXISTS prevent_final_publication_statement_evidence_mutation();
        DROP TABLE IF EXISTS publication_statement_evidence;

        CREATE OR REPLACE FUNCTION validate_correction_replacement() RETURNS trigger AS $$
        DECLARE parent_manifest UUID; DECLARE replacement_status publication_status;
        BEGIN
          SELECT supersedes_manifest_id, status INTO parent_manifest, replacement_status
          FROM publication_manifests WHERE id = NEW.replacement_manifest_id;
          IF NOT FOUND OR parent_manifest <> NEW.original_manifest_id OR replacement_status <> 'published' THEN
            RAISE EXCEPTION 'correction replacement must be published and supersede the original';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
