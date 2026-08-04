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
        assert observed[enriched_date.isoformat()] == "enriched"
        assert observed[lookalike_date.isoformat()] == "context_only"
    finally:
        command.upgrade(alembic_config, "head")
        engine.dispose()


@pytest.mark.integration
def test_the_coverage_index_migration_backfills_an_existing_archive(
    session: Session, tmp_path: Path, migrated_database: str
) -> None:
    """Upgrading a populated archive must not leave every date reporting
    coverage_not_indexed until someone remembers to rebuild."""
    from datetime import date

    from alembic import command
    from alembic.config import Config
    from app.adapters.base import LocalFilesystemRawSourceStore
    from app.batch_publication import (
        CONTEXT_BATCH_KIND,
        run_context_batch,
        start_batch_run,
    )
    from app.services import LocalFilesystemPublishedProfileStore
    from app.un_wpp import ingest_un_wpp, review_un_wpp
    from tests.conftest import SERVICE_ROOT

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    fixture = (
        Path(__file__).resolve().parents[3]
        / "data"
        / "fixtures"
        / "un-wpp"
        / "wpp2024-world-1950-2025.csv"
    )
    result = ingest_un_wpp(
        session,
        fixture_path=fixture,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )
    assert result.source_release_id is not None
    review_un_wpp(session, result.source_release_id)
    session.commit()

    context_dates = [date(1977, 6, 1), date(1977, 6, 2)]
    run = start_batch_run(
        session,
        kind=CONTEXT_BATCH_KIND,
        requested={"dates": [value.isoformat() for value in context_dates]},
    )
    run_context_batch(session, store=store, dates=context_dates, batch_run=run)
    session.commit()
    session.close()

    alembic_config = Config(str(SERVICE_ROOT / "alembic.ini"))
    alembic_config.attributes["database_url"] = migrated_database
    command.stamp(alembic_config, "head")
    # Drop the index the way an upgrade from before it would find things:
    # the archive exists, the table does not.
    command.downgrade(alembic_config, "20260726_0013")

    engine = create_engine(migrated_database)
    try:
        assert "coverage_entries" not in inspect(engine).get_table_names()
        command.upgrade(alembic_config, "head")
        with engine.connect() as connection:
            rows = {
                str(row[0]): (row[1], row[2], row[3])
                for row in connection.execute(
                    text(
                        "SELECT profile_date::text, publication_tier::text, "
                        "has_recorded_event, sections::text FROM coverage_entries"
                    )
                ).all()
            }
        assert set(rows) == {value.isoformat() for value in context_dates}
        for tier, has_recorded_event, sections in rows.values():
            assert tier == "context_only"
            assert has_recorded_event is False
            assert '"typical_day_in_this_year": 2' in sections
            assert '"wider_historical_context": 3' in sections
            # All seven keys, zero-filled, exactly as the publisher emits.
            assert '"curated_claims": 0' in sections
    finally:
        command.upgrade(alembic_config, "head")
        engine.dispose()


def _constraint_definitions(connection: object, table: str) -> dict[str, str]:
    return {
        name: definition
        for name, definition in connection.execute(  # type: ignore[attr-defined]
            text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = :table ::regclass"
            ),
            {"table": table},
        )
    }


@pytest.mark.integration
def test_the_identity_adjudication_migration_enforces_its_invariants(
    migrated_database: str,
) -> None:
    """The adjudication's rules are enforced by the database, not only the writer.

    A rule that lives only in the Python writer is one direct INSERT away from
    being untrue, and this table's entire value is that what it records actually
    happened and has not been rewritten since.
    """
    engine = create_engine(migrated_database)
    try:
        inspector = inspect(engine)
        assert "event_identity_adjudications" in set(inspector.get_table_names())
        with engine.connect() as connection:
            definitions = _constraint_definitions(
                connection, "event_identity_adjudications"
            )

        # Canonical ordering and the self-pair rejection are the same constraint.
        assert (
            "event_a_id < event_b_id"
            in definitions["event_identity_adjudication_canonical_pair"]
        )
        # A survivor is required exactly for the directional outcomes, and must
        # be a member of the pair.
        assert "event_identity_adjudication_survivor_required" in definitions
        assert "event_identity_adjudication_survivor_in_pair" in definitions
        assert "event_identity_adjudication_reviewer_present" in definitions

        # RESTRICT everywhere: the audit trail behind a decision cannot be
        # deleted out from under it.
        foreign_keys = inspector.get_foreign_keys("event_identity_adjudications")
        assert foreign_keys
        for foreign_key in foreign_keys:
            assert (foreign_key["options"] or {}).get("ondelete") == "RESTRICT"

        # The featured-event decision namespace is admitted by the editorial
        # vocabulary, and the existing published sections still are.
        with engine.connect() as connection:
            editorial = _constraint_definitions(connection, "editorial_selections")
        section_key = next(
            definition
            for definition in editorial.values()
            if "section_key" in definition
        )
        assert "'featured_event'" in section_key
        assert "'recorded_on_this_date'" in section_key

        # Append-only history: one row per (pair, version).
        indexes = {
            index["name"]: index
            for index in inspector.get_indexes("event_identity_adjudications")
        }
        history = indexes["event_identity_adjudication_history"]
        assert history["unique"] is True
        assert history["column_names"] == [
            "event_a_id",
            "event_b_id",
            "decision_version",
        ]
    finally:
        engine.dispose()
