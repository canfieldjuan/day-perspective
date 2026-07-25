from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session


@pytest.mark.integration
def test_migration_from_empty_state_creates_every_required_foundational_table(migrated_database: str) -> None:
    engine = create_engine(migrated_database)
    try:
        inspector = inspect(engine)
        required = {
            "sources", "source_releases", "source_lineage", "claims", "resolved_claims", "events",
            "event_times", "geographies", "geography_versions", "event_locations", "people",
            "organizations", "entity_aliases", "external_identifiers", "metrics", "observations",
            "event_impacts", "metric_coverage", "quality_assessments", "methodologies", "derived_values",
            "publication_manifests", "publication_statement_evidence", "day_profiles", "pipeline_runs",
            "quality_checks", "review_tasks", "corrections",
        }
        assert required <= set(inspector.get_table_names())
        checksum_constraints = [
            constraint
            for constraint in inspector.get_unique_constraints("source_releases")
            if set(constraint["column_names"] or ())
            == {"source_id", "raw_checksum_sha256"}
        ]
        assert len(checksum_constraints) == 1
        statement_evidence_columns = {
            column["name"]
            for column in inspector.get_columns("publication_statement_evidence")
        }
        assert {"evidence_snapshot", "evidence_snapshot_hash"} <= statement_evidence_columns
        quality_assessment_columns = {
            column["name"] for column in inspector.get_columns("quality_assessments")
        }
        assert "target_methodology_id" in quality_assessment_columns
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT postgis_version()"))
    finally:
        engine.dispose()


@pytest.mark.integration
def test_publication_tier_backfill_derives_from_statement_evidence(
    session: Session, migrated_database: str, tmp_path: Path
) -> None:
    """The 0012 backfill never executes against a fresh database, so prove it
    against real published rows: an established archive must gain honest
    tiers from its immutable statement-evidence rows, not a blanket default."""
    from datetime import date

    from alembic import command
    from alembic.config import Config
    from app.models import ProfileType
    from app.services import (
        LocalFilesystemPublishedProfileStore,
        PublicationStatementEvidenceInput,
        create_claim,
        publish_day_profile,
        resolve_claim,
    )
    from tests.conftest import SERVICE_ROOT
    from tests.helpers import source_release

    store = LocalFilesystemPublishedProfileStore(tmp_path)
    release = source_release(session)

    def mapping(label: str, path: str) -> PublicationStatementEvidenceInput:
        claim = create_claim(
            session,
            source_release_id=release.id,
            source_record_locator=f"record:backfill-{label}",
            claim_type="synthetic_assertion",
            assertion_text=f"Synthetic {label} statement.",
        )
        resolved = resolve_claim(
            session,
            canonical_key=f"test:backfill-{label}",
            resolved_value={"statement": label},
            rationale="Test-only backfill provenance.",
            supporting_claim_ids=[claim.id],
        )
        return PublicationStatementEvidenceInput(
            statement_path=path, resolved_claim_id=resolved.id
        )

    context_date = date(1969, 7, 20)
    enriched_date = date(1970, 1, 2)
    lookalike_date = date(1972, 3, 3)
    publish_day_profile(
        session,
        store=store,
        profile_date=context_date,
        profile_type=ProfileType.STANDARD_STATISTICAL,
        payload={
            "schema_version": "1",
            "date": context_date.isoformat(),
            "profile_type": "standard_statistical",
            "sections": {
                "evidence_notes": [
                    {"statement_id": "note", "statement": "context"}
                ]
            },
        },
        statement_evidence=[mapping("context", "/sections/evidence_notes/0")],
    )
    publish_day_profile(
        session,
        store=store,
        profile_date=enriched_date,
        profile_type=ProfileType.STANDARD_STATISTICAL,
        payload={
            "schema_version": "1",
            "date": enriched_date.isoformat(),
            "profile_type": "standard_statistical",
            "sections": {
                "recorded_on_this_date": [
                    {"statement_id": "event", "statement": "recorded"}
                ]
            },
        },
        statement_evidence=[
            mapping("recorded", "/sections/recorded_on_this_date/0")
        ],
    )
    # A section key that merely resembles the recorded-event path must not be
    # backfilled as enriched: every underscore in a LIKE pattern is a
    # single-character wildcard. Evidence is immutable after publication, so
    # the lookalike path is established at publication time.
    publish_day_profile(
        session,
        store=store,
        profile_date=lookalike_date,
        profile_type=ProfileType.STANDARD_STATISTICAL,
        payload={
            "schema_version": "1",
            "date": lookalike_date.isoformat(),
            "profile_type": "standard_statistical",
            "sections": {
                "recordedXonYthisZdate": [
                    {"statement_id": "lookalike", "statement": "lookalike"}
                ]
            },
        },
        statement_evidence=[
            mapping("lookalike", "/sections/recordedXonYthisZdate/0")
        ],
    )
    session.commit()
    session.close()

    alembic_config = Config(str(SERVICE_ROOT / "alembic.ini"))
    alembic_config.attributes["database_url"] = migrated_database
    # The session fixture truncates every table in the schema, including
    # alembic_version, so restore the version record before exercising the
    # migration. The schema itself is at head.
    command.stamp(alembic_config, "head")
    # Target the revision that introduced tiers explicitly: a relative step
    # would follow whatever migration happens to be head today, and a failed
    # assertion below must never leave the schema downgraded for the rest of
    # the suite (hence the restoration in the finally block).
    command.downgrade(alembic_config, "20260724_0011")

    engine = create_engine(migrated_database)
    try:
        assert "publication_tier" not in {
            column["name"]
            for column in inspect(engine).get_columns("publication_manifests")
        }
        command.upgrade(alembic_config, "head")
        with engine.connect() as connection:
            observed: dict[str, str] = {
                str(row[0]): str(row[1])
                for row in connection.execute(
                    text(
                        "SELECT profile_date::text, publication_tier::text "
                        "FROM publication_manifests"
                    )
                ).all()
            }
        assert observed[context_date.isoformat()] == "context_only"
        assert observed[enriched_date.isoformat()] == "reviewed_enriched"
        assert observed[lookalike_date.isoformat()] == "context_only"
    finally:
        command.upgrade(alembic_config, "head")
        engine.dispose()
