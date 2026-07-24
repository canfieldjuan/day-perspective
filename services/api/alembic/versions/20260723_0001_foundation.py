"""Create the provenance-first Day Perspective foundation.

Revision ID: 20260723_0001
Revises:
Create Date: 2026-07-23
"""

from alembic import op

revision = "20260723_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    for statement in (
        "CREATE TYPE claim_assertion_status AS ENUM ('imported','candidate','in_review','accepted','rejected','superseded','retracted')",
        "CREATE TYPE temporal_precision AS ENUM ('day','month','year','decade','interval','unknown')",
        "CREATE TYPE temporal_assignment AS ENUM ('direct_record','reported','inferred','uniform_period_allocation','modeled_period_allocation','editorial_context','unknown')",
        "CREATE TYPE date_role AS ENUM ('occurred','began','ended','reported','discovered','published','predicted','commemorated')",
        "CREATE TYPE data_status AS ENUM ('reported','estimated','modeled','provisional','final','missing','withdrawn')",
        "CREATE TYPE missing_reason AS ENUM ('not_collected','not_available','not_applicable','withheld','invalid','unknown')",
        "CREATE TYPE resolution_method AS ENUM ('single_source','corroborated','editorial_review','methodological_derivation')",
        "CREATE TYPE source_lineage_relationship AS ENUM ('republished','transcribed','extracted','aggregated','derived')",
        "CREATE TYPE comparability_status AS ENUM ('comparable','partially_comparable','not_comparable','unknown')",
        "CREATE TYPE impact_directness AS ENUM ('direct','indirect','modeled','contextual')",
        "CREATE TYPE publication_status AS ENUM ('draft','published','superseded','withdrawn')",
        "CREATE TYPE profile_type AS ENUM ('limited_historical','standard_statistical','enhanced_structured')",
        "CREATE TYPE legal_review_status AS ENUM ('not_required','pending','approved','restricted','rejected')",
    ):
        op.execute(statement)

    op.execute(
        """
        CREATE TABLE pipeline_runs (
          id UUID PRIMARY KEY, pipeline_name VARCHAR(160) NOT NULL, code_version VARCHAR(160) NOT NULL,
          configuration_hash VARCHAR(64) NOT NULL CHECK (configuration_hash ~ '^[0-9a-f]{64}$'),
          status VARCHAR(32) NOT NULL CHECK (status IN ('running','succeeded','failed','cancelled')),
          started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, completed_at TIMESTAMPTZ,
          details JSONB NOT NULL DEFAULT '{}'::jsonb, CHECK (completed_at IS NULL OR completed_at >= started_at)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE methodologies (
          id UUID PRIMARY KEY, slug VARCHAR(160) NOT NULL, version VARCHAR(80) NOT NULL, name TEXT NOT NULL,
          description TEXT NOT NULL, method_kind VARCHAR(80) NOT NULL DEFAULT 'editorial_or_calculation',
          formula TEXT, code_version VARCHAR(160) NOT NULL,
          definition_hash VARCHAR(64) NOT NULL CHECK (definition_hash ~ '^[0-9a-f]{64}$'),
          legal_review_status legal_review_status NOT NULL DEFAULT 'not_required',
          created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE (slug, version)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE sources (
          id UUID PRIMARY KEY, slug VARCHAR(160) NOT NULL UNIQUE, name TEXT NOT NULL, publisher TEXT,
          canonical_url TEXT, legal_review_status legal_review_status NOT NULL DEFAULT 'pending',
          created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        """
        CREATE TABLE source_releases (
          id UUID PRIMARY KEY, source_id UUID NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
          release_label VARCHAR(240) NOT NULL, source_url TEXT NOT NULL, published_at TIMESTAMPTZ,
          retrieved_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
          ingested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, raw_storage_uri TEXT NOT NULL,
          raw_checksum_sha256 VARCHAR(64) NOT NULL CHECK (raw_checksum_sha256 ~ '^[0-9a-f]{64}$'),
          raw_record_count INTEGER NOT NULL CHECK (raw_record_count >= 0),
          legal_review_status legal_review_status NOT NULL DEFAULT 'pending',
          pipeline_run_id UUID REFERENCES pipeline_runs(id) ON DELETE RESTRICT,
          metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
          UNIQUE (source_id, release_label), UNIQUE (source_id, raw_checksum_sha256)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE source_lineage (
          id UUID PRIMARY KEY, child_release_id UUID NOT NULL REFERENCES source_releases(id) ON DELETE RESTRICT,
          parent_release_id UUID NOT NULL REFERENCES source_releases(id) ON DELETE RESTRICT,
          relationship source_lineage_relationship NOT NULL,
          methodology_id UUID REFERENCES methodologies(id) ON DELETE RESTRICT, note TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CHECK (child_release_id <> parent_release_id), UNIQUE (child_release_id, parent_release_id, relationship)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE claims (
          id UUID PRIMARY KEY, source_release_id UUID NOT NULL REFERENCES source_releases(id) ON DELETE RESTRICT,
          source_record_locator TEXT NOT NULL, assertion_status claim_assertion_status NOT NULL DEFAULT 'imported',
          claim_type VARCHAR(120) NOT NULL, assertion_text TEXT, assertion_json JSONB,
          temporal_start DATE, temporal_end DATE, temporal_precision temporal_precision NOT NULL DEFAULT 'unknown',
          temporal_assignment temporal_assignment NOT NULL DEFAULT 'unknown', date_role date_role,
          data_status data_status NOT NULL DEFAULT 'reported', missing_reason missing_reason,
          supersedes_claim_id UUID REFERENCES claims(id) ON DELETE RESTRICT,
          pipeline_run_id UUID REFERENCES pipeline_runs(id) ON DELETE RESTRICT,
          imported_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CHECK (temporal_end IS NULL OR temporal_start IS NULL OR temporal_end >= temporal_start),
          CHECK (supersedes_claim_id IS NULL OR supersedes_claim_id <> id),
          CHECK ((data_status = 'missing' AND assertion_text IS NULL AND assertion_json IS NULL AND missing_reason IS NOT NULL)
            OR (data_status <> 'missing' AND missing_reason IS NULL))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE resolved_claims (
          id UUID PRIMARY KEY, canonical_key VARCHAR(240) NOT NULL, version INTEGER NOT NULL CHECK (version > 0),
          resolved_value JSONB NOT NULL, resolution_method resolution_method NOT NULL,
          comparability_status comparability_status NOT NULL DEFAULT 'unknown', rationale TEXT NOT NULL,
          methodology_id UUID REFERENCES methodologies(id) ON DELETE RESTRICT,
          supersedes_resolved_claim_id UUID REFERENCES resolved_claims(id) ON DELETE RESTRICT,
          resolved_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CHECK (supersedes_resolved_claim_id IS NULL OR supersedes_resolved_claim_id <> id),
          UNIQUE (canonical_key, version)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE resolved_claim_evidence (
          resolved_claim_id UUID NOT NULL REFERENCES resolved_claims(id) ON DELETE RESTRICT,
          claim_id UUID NOT NULL REFERENCES claims(id) ON DELETE RESTRICT,
          stance VARCHAR(16) NOT NULL CHECK (stance IN ('supporting','dissenting')), note TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (resolved_claim_id, claim_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE geographies (
          id UUID PRIMARY KEY, stable_key VARCHAR(160) NOT NULL UNIQUE, geography_kind VARCHAR(80) NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        """
        CREATE TABLE geography_versions (
          id UUID PRIMARY KEY, geography_id UUID NOT NULL REFERENCES geographies(id) ON DELETE RESTRICT,
          provenance_resolved_claim_id UUID NOT NULL REFERENCES resolved_claims(id) ON DELETE RESTRICT,
          name TEXT NOT NULL, identifier_code VARCHAR(160), valid_from DATE NOT NULL, valid_to DATE,
          boundary_geometry geometry(MULTIPOLYGON, 4326), created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CHECK (valid_to IS NULL OR valid_to >= valid_from)
        )
        """
    )
    op.execute(
        """
        ALTER TABLE geography_versions ADD CONSTRAINT geography_versions_no_overlap
        EXCLUDE USING gist (geography_id WITH =,
          daterange(valid_from, COALESCE(valid_to, 'infinity'::date), '[]') WITH &&)
        """
    )
    op.execute(
        """
        CREATE TABLE events (
          id UUID PRIMARY KEY, resolved_claim_id UUID NOT NULL UNIQUE REFERENCES resolved_claims(id) ON DELETE RESTRICT,
          event_type VARCHAR(120) NOT NULL, canonical_title TEXT NOT NULL, summary TEXT,
          data_status data_status NOT NULL DEFAULT 'reported', created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        """
        CREATE TABLE event_times (
          id UUID PRIMARY KEY, event_id UUID NOT NULL REFERENCES events(id) ON DELETE RESTRICT,
          provenance_resolved_claim_id UUID NOT NULL REFERENCES resolved_claims(id) ON DELETE RESTRICT,
          start_date DATE NOT NULL, end_date DATE, temporal_precision temporal_precision NOT NULL,
          temporal_assignment temporal_assignment NOT NULL, date_role date_role NOT NULL,
          is_primary BOOLEAN NOT NULL DEFAULT FALSE, display_label TEXT,
          CHECK (end_date IS NULL OR end_date >= start_date)
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX event_times_one_primary_per_event ON event_times (event_id) WHERE is_primary")
    op.execute(
        """
        CREATE TABLE event_locations (
          id UUID PRIMARY KEY, event_id UUID NOT NULL REFERENCES events(id) ON DELETE RESTRICT,
          geography_version_id UUID REFERENCES geography_versions(id) ON DELETE RESTRICT,
          provenance_resolved_claim_id UUID NOT NULL REFERENCES resolved_claims(id) ON DELETE RESTRICT,
          point_geometry geometry(POINT, 4326), location_role VARCHAR(80) NOT NULL DEFAULT 'primary', display_label TEXT,
          CHECK (geography_version_id IS NOT NULL OR point_geometry IS NOT NULL)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE people (
          id UUID PRIMARY KEY, resolved_claim_id UUID NOT NULL UNIQUE REFERENCES resolved_claims(id) ON DELETE RESTRICT,
          canonical_name TEXT NOT NULL, biography_summary TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        """
        CREATE TABLE organizations (
          id UUID PRIMARY KEY, resolved_claim_id UUID NOT NULL UNIQUE REFERENCES resolved_claims(id) ON DELETE RESTRICT,
          canonical_name TEXT NOT NULL, organization_kind VARCHAR(120), created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        """
        CREATE TABLE entity_aliases (
          id UUID PRIMARY KEY, person_id UUID REFERENCES people(id) ON DELETE RESTRICT,
          organization_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
          geography_id UUID REFERENCES geographies(id) ON DELETE RESTRICT,
          event_id UUID REFERENCES events(id) ON DELETE RESTRICT, alias TEXT NOT NULL, language_code VARCHAR(16),
          provenance_resolved_claim_id UUID NOT NULL REFERENCES resolved_claims(id) ON DELETE RESTRICT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CHECK (num_nonnulls(person_id, organization_id, geography_id, event_id) = 1)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE external_identifiers (
          id UUID PRIMARY KEY, namespace VARCHAR(120) NOT NULL, external_id TEXT NOT NULL,
          person_id UUID REFERENCES people(id) ON DELETE RESTRICT,
          organization_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
          geography_id UUID REFERENCES geographies(id) ON DELETE RESTRICT,
          event_id UUID REFERENCES events(id) ON DELETE RESTRICT,
          provenance_resolved_claim_id UUID NOT NULL REFERENCES resolved_claims(id) ON DELETE RESTRICT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CHECK (num_nonnulls(person_id, organization_id, geography_id, event_id) = 1), UNIQUE (namespace, external_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE metrics (
          id UUID PRIMARY KEY, metric_key VARCHAR(160) NOT NULL UNIQUE, display_name TEXT NOT NULL, unit TEXT NOT NULL,
          definition TEXT NOT NULL, data_status data_status NOT NULL DEFAULT 'reported',
          provenance_resolved_claim_id UUID NOT NULL REFERENCES resolved_claims(id) ON DELETE RESTRICT,
          methodology_id UUID REFERENCES methodologies(id) ON DELETE RESTRICT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        """
        CREATE TABLE observations (
          id UUID PRIMARY KEY, metric_id UUID NOT NULL REFERENCES metrics(id) ON DELETE RESTRICT,
          geography_version_id UUID REFERENCES geography_versions(id) ON DELETE RESTRICT,
          source_release_id UUID NOT NULL REFERENCES source_releases(id) ON DELETE RESTRICT,
          provenance_resolved_claim_id UUID REFERENCES resolved_claims(id) ON DELETE RESTRICT,
          period_start DATE NOT NULL, period_end DATE, temporal_precision temporal_precision NOT NULL,
          temporal_assignment temporal_assignment NOT NULL, date_role date_role NOT NULL DEFAULT 'reported',
          value_numeric NUMERIC, value_text TEXT, data_status data_status NOT NULL, missing_reason missing_reason,
          created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CHECK (period_end IS NULL OR period_end >= period_start),
          CHECK ((data_status = 'missing' AND value_numeric IS NULL AND value_text IS NULL AND missing_reason IS NOT NULL)
             OR (data_status <> 'missing' AND (value_numeric IS NOT NULL OR value_text IS NOT NULL) AND missing_reason IS NULL))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE event_impacts (
          id UUID PRIMARY KEY, event_id UUID NOT NULL REFERENCES events(id) ON DELETE RESTRICT,
          metric_id UUID REFERENCES metrics(id) ON DELETE RESTRICT,
          provenance_resolved_claim_id UUID NOT NULL REFERENCES resolved_claims(id) ON DELETE RESTRICT,
          methodology_id UUID REFERENCES methodologies(id) ON DELETE RESTRICT,
          impact_directness impact_directness NOT NULL, narrative TEXT NOT NULL, value_numeric NUMERIC,
          data_status data_status NOT NULL DEFAULT 'reported', missing_reason missing_reason,
          created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CHECK ((data_status = 'missing' AND value_numeric IS NULL AND missing_reason IS NOT NULL)
             OR (data_status <> 'missing' AND missing_reason IS NULL))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE metric_coverage (
          id UUID PRIMARY KEY, metric_id UUID NOT NULL REFERENCES metrics(id) ON DELETE RESTRICT,
          geography_version_id UUID REFERENCES geography_versions(id) ON DELETE RESTRICT,
          source_release_id UUID NOT NULL REFERENCES source_releases(id) ON DELETE RESTRICT,
          provenance_resolved_claim_id UUID NOT NULL REFERENCES resolved_claims(id) ON DELETE RESTRICT,
          period_start DATE NOT NULL, period_end DATE NOT NULL,
          coverage_fraction NUMERIC NOT NULL CHECK (coverage_fraction >= 0 AND coverage_fraction <= 1),
          data_status data_status NOT NULL, missing_reason missing_reason,
          comparability_status comparability_status NOT NULL DEFAULT 'unknown',
          CHECK (period_end >= period_start),
          CHECK ((data_status = 'missing' AND missing_reason IS NOT NULL) OR (data_status <> 'missing' AND missing_reason IS NULL))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE derived_values (
          id UUID PRIMARY KEY, metric_id UUID REFERENCES metrics(id) ON DELETE RESTRICT,
          geography_version_id UUID REFERENCES geography_versions(id) ON DELETE RESTRICT,
          methodology_id UUID NOT NULL REFERENCES methodologies(id) ON DELETE RESTRICT,
          provenance_resolved_claim_id UUID REFERENCES resolved_claims(id) ON DELETE RESTRICT,
          value_kind VARCHAR(120) NOT NULL, period_start DATE NOT NULL, period_end DATE,
          temporal_assignment temporal_assignment NOT NULL, value_numeric NUMERIC, value_json JSONB,
          data_status data_status NOT NULL, missing_reason missing_reason,
          comparability_status comparability_status NOT NULL,
          input_fingerprint VARCHAR(64) NOT NULL CHECK (input_fingerprint ~ '^[0-9a-f]{64}$'),
          calculation_version VARCHAR(160) NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CHECK (period_end IS NULL OR period_end >= period_start),
          CHECK ((data_status = 'missing' AND value_numeric IS NULL AND value_json IS NULL AND missing_reason IS NOT NULL)
             OR (data_status <> 'missing' AND (value_numeric IS NOT NULL OR value_json IS NOT NULL) AND missing_reason IS NULL))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE derived_value_inputs (
          id UUID PRIMARY KEY, derived_value_id UUID NOT NULL REFERENCES derived_values(id) ON DELETE RESTRICT,
          observation_id UUID REFERENCES observations(id) ON DELETE RESTRICT,
          resolved_claim_id UUID REFERENCES resolved_claims(id) ON DELETE RESTRICT,
          input_role VARCHAR(32) NOT NULL CHECK (input_role IN ('primary','supporting','comparison')),
          created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CHECK (num_nonnulls(observation_id, resolved_claim_id) = 1)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE quality_assessments (
          id UUID PRIMARY KEY, source_release_id UUID REFERENCES source_releases(id) ON DELETE RESTRICT,
          claim_id UUID REFERENCES claims(id) ON DELETE RESTRICT,
          observation_id UUID REFERENCES observations(id) ON DELETE RESTRICT,
          derived_value_id UUID REFERENCES derived_values(id) ON DELETE RESTRICT,
          methodology_id UUID REFERENCES methodologies(id) ON DELETE RESTRICT,
          legal_review_status legal_review_status NOT NULL DEFAULT 'not_required', assessment_kind VARCHAR(120) NOT NULL,
          score NUMERIC CHECK (score >= 0 AND score <= 1), findings JSONB NOT NULL DEFAULT '{}'::jsonb,
          assessed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CHECK (num_nonnulls(source_release_id, claim_id, observation_id, derived_value_id) >= 1)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE publication_manifests (
          id UUID PRIMARY KEY, profile_date DATE NOT NULL, profile_type profile_type NOT NULL,
          version INTEGER NOT NULL CHECK (version > 0), status publication_status NOT NULL DEFAULT 'draft',
          content_hash VARCHAR(64) NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
          source_snapshot_hash VARCHAR(64) NOT NULL CHECK (source_snapshot_hash ~ '^[0-9a-f]{64}$'),
          storage_uri TEXT NOT NULL, methodology_id UUID REFERENCES methodologies(id) ON DELETE RESTRICT,
          code_version VARCHAR(160) NOT NULL,
          supersedes_manifest_id UUID REFERENCES publication_manifests(id) ON DELETE RESTRICT,
          metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, published_at TIMESTAMPTZ,
          CHECK (supersedes_manifest_id IS NULL OR supersedes_manifest_id <> id),
          CHECK ((status = 'published' AND published_at IS NOT NULL) OR status <> 'published'),
          CHECK ((profile_type = 'limited_historical' AND profile_date BETWEEN DATE '1900-01-01' AND DATE '1949-12-31')
            OR (profile_type = 'standard_statistical' AND profile_date BETWEEN DATE '1950-01-01' AND DATE '1988-12-31')
            OR (profile_type = 'enhanced_structured' AND profile_date BETWEEN DATE '1989-01-01' AND DATE '2025-12-31')),
          UNIQUE (profile_date, profile_type, version)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE day_profiles (
          id UUID PRIMARY KEY, profile_date DATE NOT NULL, profile_type profile_type NOT NULL,
          publication_manifest_id UUID NOT NULL UNIQUE REFERENCES publication_manifests(id) ON DELETE RESTRICT,
          content_hash VARCHAR(64) NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
          supersedes_day_profile_id UUID REFERENCES day_profiles(id) ON DELETE RESTRICT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CHECK (supersedes_day_profile_id IS NULL OR supersedes_day_profile_id <> id),
          CHECK ((profile_type = 'limited_historical' AND profile_date BETWEEN DATE '1900-01-01' AND DATE '1949-12-31')
            OR (profile_type = 'standard_statistical' AND profile_date BETWEEN DATE '1950-01-01' AND DATE '1988-12-31')
            OR (profile_type = 'enhanced_structured' AND profile_date BETWEEN DATE '1989-01-01' AND DATE '2025-12-31'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE quality_checks (
          id UUID PRIMARY KEY, pipeline_run_id UUID NOT NULL REFERENCES pipeline_runs(id) ON DELETE RESTRICT,
          check_name VARCHAR(160) NOT NULL, status VARCHAR(32) NOT NULL CHECK (status IN ('passed','failed','warning','skipped')),
          subject_type VARCHAR(80) NOT NULL, subject_id UUID, details JSONB NOT NULL DEFAULT '{}'::jsonb,
          checked_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        """
        CREATE TABLE review_tasks (
          id UUID PRIMARY KEY, claim_id UUID REFERENCES claims(id) ON DELETE RESTRICT,
          resolved_claim_id UUID REFERENCES resolved_claims(id) ON DELETE RESTRICT,
          status VARCHAR(32) NOT NULL CHECK (status IN ('open','in_progress','resolved','dismissed')),
          priority VARCHAR(32) NOT NULL CHECK (priority IN ('low','normal','high','blocking')),
          rationale TEXT NOT NULL, assigned_to TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
          completed_at TIMESTAMPTZ,
          CHECK (num_nonnulls(claim_id, resolved_claim_id) = 1), CHECK (completed_at IS NULL OR completed_at >= created_at)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE corrections (
          id UUID PRIMARY KEY, correction_claim_id UUID REFERENCES claims(id) ON DELETE RESTRICT,
          original_manifest_id UUID NOT NULL REFERENCES publication_manifests(id) ON DELETE RESTRICT,
          replacement_manifest_id UUID NOT NULL REFERENCES publication_manifests(id) ON DELETE RESTRICT,
          rationale TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CHECK (original_manifest_id <> replacement_manifest_id), UNIQUE (original_manifest_id, replacement_manifest_id)
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_source_release_mutation() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'source_releases are immutable after ingestion'; END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER source_releases_immutable BEFORE UPDATE OR DELETE ON source_releases
          FOR EACH ROW EXECUTE FUNCTION prevent_source_release_mutation();

        CREATE FUNCTION prevent_final_manifest_mutation() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' OR OLD.status IN ('published','superseded','withdrawn') THEN
            RAISE EXCEPTION 'final publication manifests are immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER publication_manifests_final_immutable BEFORE UPDATE OR DELETE ON publication_manifests
          FOR EACH ROW EXECUTE FUNCTION prevent_final_manifest_mutation();

        CREATE FUNCTION validate_day_profile_manifest() RETURNS trigger AS $$
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
        CREATE TRIGGER day_profiles_validate_manifest BEFORE INSERT OR UPDATE ON day_profiles
          FOR EACH ROW EXECUTE FUNCTION validate_day_profile_manifest();

        CREATE FUNCTION prevent_published_day_profile_delete() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'day profiles attached to published manifests are immutable'; END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER day_profiles_prevent_published_delete BEFORE DELETE ON day_profiles
          FOR EACH ROW EXECUTE FUNCTION prevent_published_day_profile_delete();

        CREATE FUNCTION validate_correction_replacement() RETURNS trigger AS $$
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
        CREATE TRIGGER corrections_validate_replacement BEFORE INSERT OR UPDATE ON corrections
          FOR EACH ROW EXECUTE FUNCTION validate_correction_replacement();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS corrections CASCADE;
        DROP TABLE IF EXISTS review_tasks CASCADE;
        DROP TABLE IF EXISTS quality_checks CASCADE;
        DROP TABLE IF EXISTS day_profiles CASCADE;
        DROP TABLE IF EXISTS publication_manifests CASCADE;
        DROP TABLE IF EXISTS quality_assessments CASCADE;
        DROP TABLE IF EXISTS derived_value_inputs CASCADE;
        DROP TABLE IF EXISTS derived_values CASCADE;
        DROP TABLE IF EXISTS metric_coverage CASCADE;
        DROP TABLE IF EXISTS event_impacts CASCADE;
        DROP TABLE IF EXISTS observations CASCADE;
        DROP TABLE IF EXISTS metrics CASCADE;
        DROP TABLE IF EXISTS external_identifiers CASCADE;
        DROP TABLE IF EXISTS entity_aliases CASCADE;
        DROP TABLE IF EXISTS organizations CASCADE;
        DROP TABLE IF EXISTS people CASCADE;
        DROP TABLE IF EXISTS event_locations CASCADE;
        DROP TABLE IF EXISTS event_times CASCADE;
        DROP TABLE IF EXISTS events CASCADE;
        DROP TABLE IF EXISTS geography_versions CASCADE;
        DROP TABLE IF EXISTS geographies CASCADE;
        DROP TABLE IF EXISTS resolved_claim_evidence CASCADE;
        DROP TABLE IF EXISTS resolved_claims CASCADE;
        DROP TABLE IF EXISTS claims CASCADE;
        DROP TABLE IF EXISTS source_lineage CASCADE;
        DROP TABLE IF EXISTS source_releases CASCADE;
        DROP TABLE IF EXISTS sources CASCADE;
        DROP TABLE IF EXISTS methodologies CASCADE;
        DROP TABLE IF EXISTS pipeline_runs CASCADE;
        DROP FUNCTION IF EXISTS validate_correction_replacement();
        DROP FUNCTION IF EXISTS prevent_published_day_profile_delete();
        DROP FUNCTION IF EXISTS validate_day_profile_manifest();
        DROP FUNCTION IF EXISTS prevent_final_manifest_mutation();
        DROP FUNCTION IF EXISTS prevent_source_release_mutation();
        DROP TYPE IF EXISTS legal_review_status;
        DROP TYPE IF EXISTS profile_type;
        DROP TYPE IF EXISTS publication_status;
        DROP TYPE IF EXISTS impact_directness;
        DROP TYPE IF EXISTS comparability_status;
        DROP TYPE IF EXISTS source_lineage_relationship;
        DROP TYPE IF EXISTS resolution_method;
        DROP TYPE IF EXISTS missing_reason;
        DROP TYPE IF EXISTS data_status;
        DROP TYPE IF EXISTS date_role;
        DROP TYPE IF EXISTS temporal_assignment;
        DROP TYPE IF EXISTS temporal_precision;
        DROP TYPE IF EXISTS claim_assertion_status;
        """
    )
