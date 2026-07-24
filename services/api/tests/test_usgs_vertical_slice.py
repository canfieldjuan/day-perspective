from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app import main
from app.database import get_session
from app.models import (
    Claim,
    ClaimAssertionStatus,
    Event,
    EventLocation,
    EventTime,
    GeographyVersion,
    PipelineRun,
    PublicationManifest,
    PublicationStatementEvidence,
    QualityAssessment,
    QualityCheck,
    RawSourceRecord,
    ResolvedClaim,
    ResolvedClaimEvidence,
    ReviewTask,
    Source,
    SourceRelease,
)
from app.services import (
    LocalFilesystemPublishedProfileStore,
    content_hash,
    create_source_release,
)
from app.usgs import (
    GOLDEN_DATE,
    EvidenceCandidate,
    LocalFilesystemRawSourceStore,
    USGSEarthquakeAdapter,
    accept_and_resolve_release,
    derive_quality,
    deterministic_resolution,
    ingest_usgs,
    publish_golden_profile,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "data/fixtures/usgs/1964-prince-william-sound.geojson"


def ingest(session: Session, tmp_path: Path):
    return ingest_usgs(
        session,
        adapter=USGSEarthquakeAdapter(),
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
        fixture_path=FIXTURE,
    )


def publish(session: Session, tmp_path: Path):
    result = ingest(session, tmp_path)
    profile = publish_golden_profile(
        session,
        store=LocalFilesystemPublishedProfileStore(tmp_path / "published"),
    )
    session.flush()
    return result, profile


def override_session(session: Session) -> Callable[[], Generator[Session]]:
    def dependency() -> Generator[Session]:
        yield session

    return dependency


def test_fixture_ingestion_records_release_raw_record_claims_run_and_check(
    session: Session, tmp_path: Path
) -> None:
    result = ingest(session, tmp_path)

    assert result.source_release_id is not None
    assert len(result.claim_ids) == 9
    assert session.scalar(select(func.count()).select_from(RawSourceRecord)) == 1
    assert session.scalar(select(PipelineRun.status)) == "succeeded"
    assert session.scalar(select(QualityCheck.status)) == "passed"
    assert session.scalar(select(func.count()).select_from(ReviewTask)) == 9
    assert set(session.scalars(select(ReviewTask.status))) == {"open"}


def test_raw_checksum_is_stable_and_matches_committed_fixture(
    session: Session, tmp_path: Path
) -> None:
    result = ingest(session, tmp_path)
    expected = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    release = session.get(SourceRelease, result.source_release_id)
    raw_record = session.scalar(select(RawSourceRecord))

    assert result.checksum == expected
    assert release is not None and release.raw_checksum_sha256 == expected
    assert raw_record is not None and raw_record.raw_checksum_sha256 == expected


def test_raw_source_record_is_immutable(session: Session, tmp_path: Path) -> None:
    ingest(session, tmp_path)
    raw_record = session.scalar(select(RawSourceRecord))
    assert raw_record is not None

    with pytest.raises(DBAPIError, match="immutable"):
        session.execute(
            update(RawSourceRecord)
            .where(RawSourceRecord.id == raw_record.id)
            .values(schema_version="rewritten")
        )
        session.flush()


def test_idempotent_pipeline_rerun_does_not_duplicate_release_or_claims(
    session: Session, tmp_path: Path
) -> None:
    first = ingest(session, tmp_path)
    second = ingest(session, tmp_path)

    assert first.source_release_id == second.source_release_id
    assert second.idempotent is True
    assert session.scalar(select(func.count()).select_from(SourceRelease)) == 1
    assert session.scalar(select(func.count()).select_from(Claim)) == 9
    assert session.scalar(select(func.count()).select_from(PipelineRun)) == 2


def test_idempotent_rerun_refuses_corrupt_raw_storage(
    session: Session, tmp_path: Path
) -> None:
    store = LocalFilesystemRawSourceStore(tmp_path / "raw")
    first = ingest_usgs(
        session,
        adapter=USGSEarthquakeAdapter(),
        raw_store=store,
        fixture_path=FIXTURE,
    )
    release = session.get(SourceRelease, first.source_release_id)
    assert release is not None
    (tmp_path / "raw" / release.raw_storage_uri).write_bytes(b"corrupt")

    with pytest.raises(RuntimeError, match="did not match"):
        ingest_usgs(
            session,
            adapter=USGSEarthquakeAdapter(),
            raw_store=store,
            fixture_path=FIXTURE,
        )

    runs = list(
        session.scalars(select(PipelineRun).order_by(PipelineRun.started_at))
    )
    assert [run.status for run in runs] == ["succeeded", "failed"]
    assert session.scalar(select(func.count()).select_from(SourceRelease)) == 1


def test_duplicate_source_record_handling_reuses_the_release(
    session: Session, tmp_path: Path
) -> None:
    ingest(session, tmp_path)
    ingest(session, tmp_path)
    assert session.scalar(select(func.count()).select_from(RawSourceRecord)) == 1


def test_claim_transformation_preserves_predicates_hash_units_and_bounds(
    session: Session, tmp_path: Path
) -> None:
    result = ingest(session, tmp_path)
    claims = {
        row.claim_type: row
        for row in session.scalars(select(Claim).order_by(Claim.claim_type))
    }

    assert set(claims) == {
        "depth",
        "epicenter_coordinates",
        "epicenter_geography",
        "event_identity",
        "event_title",
        "event_type",
        "local_civil_date",
        "magnitude",
        "occurrence_timestamp",
    }
    assert claims["magnitude"].unit == "mw"
    assert claims["magnitude"].lower_bound == claims["magnitude"].upper_bound
    assert all(row.source_record_hash_sha256 == result.checksum for row in claims.values())


def test_event_time_conversion_and_local_civil_date_assignment(
    session: Session, tmp_path: Path
) -> None:
    publish(session, tmp_path)
    event_time = session.scalar(select(EventTime))

    assert event_time is not None
    assert event_time.exact_timestamp is not None
    assert event_time.exact_timestamp.isoformat() == "1964-03-28T03:36:16+00:00"
    assert event_time.local_date == GOLDEN_DATE
    assert event_time.utc_offset_minutes == -600
    assert event_time.timezone_name == "America/Anchorage"
    assert "historical" in (event_time.interpretation or "").lower()


def test_geography_assignment_retains_version_and_point(
    session: Session, tmp_path: Path
) -> None:
    publish(session, tmp_path)
    geography = session.scalar(select(GeographyVersion))
    location = session.scalar(select(EventLocation))

    assert geography is not None and geography.name == "Alaska"
    assert geography.valid_from.isoformat() == "1959-01-03"
    assert location is not None and location.location_role == "epicenter"
    assert location.display_label is not None and "Alaska" in location.display_label


def test_supporting_and_dissenting_claims_are_classified_without_hiding_disagreement() -> None:
    decision = deterministic_resolution(
        (
            EvidenceCandidate(9.2, True, "usgs"),
            EvidenceCandidate(9.2, False, "independent"),
            EvidenceCandidate(9.0, False, "dissent"),
        ),
        tolerance=0.05,
    )

    assert decision.status == "unresolved"
    assert decision.supporting_indexes == (0, 1)
    assert decision.dissenting_indexes == (2,)


def test_dependent_lineage_is_not_counted_as_independent() -> None:
    decision = deterministic_resolution(
        (
            EvidenceCandidate("same", True, "usgs"),
            EvidenceCandidate("same", False, "usgs"),
            EvidenceCandidate("same", False, "other"),
        )
    )

    assert decision.independent_source_count == 2


def test_unbounded_disagreement_remains_unresolved() -> None:
    decision = deterministic_resolution(
        (
            EvidenceCandidate("A", True, "one"),
            EvidenceCandidate("B", False, "two"),
        )
    )
    assert decision.status == "unresolved"
    assert decision.dissenting_indexes == (1,)


def test_resolution_is_versioned_when_a_resolved_value_changes(
    session: Session, tmp_path: Path
) -> None:
    result, _ = publish(session, tmp_path)
    assert result.source_release_id is not None
    claim = session.scalar(
        select(Claim).where(
            Claim.source_release_id == result.source_release_id,
            Claim.claim_type == "magnitude",
        )
    )
    first = session.scalar(
        select(ResolvedClaim)
        .where(ResolvedClaim.canonical_key.like("%:magnitude"))
        .order_by(ResolvedClaim.version)
    )
    assert claim is not None and first is not None
    from app.services import resolve_claim

    second = resolve_claim(
        session,
        canonical_key=first.canonical_key,
        resolved_value={"value": 9.1, "scale": "mw"},
        rationale="Test-only changed resolution.",
        supporting_claim_ids=[claim.id],
        supersedes_resolved_claim_id=first.id,
    )

    assert second.version == 2
    assert second.supersedes_resolved_claim_id == first.id


def test_unchanged_value_from_new_release_creates_current_evidence_version(
    session: Session, tmp_path: Path
) -> None:
    first = ingest(session, tmp_path)
    assert first.source_release_id is not None
    accept_and_resolve_release(session, first.source_release_id)
    revised_fixture = tmp_path / "same-record-new-release.geojson"
    revised_fixture.write_bytes(FIXTURE.read_bytes() + b"\n")
    second = ingest_usgs(
        session,
        adapter=USGSEarthquakeAdapter(),
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
        fixture_path=revised_fixture,
    )
    assert second.source_release_id is not None

    resolved = accept_and_resolve_release(session, second.source_release_id)
    supporting_release = session.scalar(
        select(Claim.source_release_id)
        .join(ResolvedClaimEvidence, ResolvedClaimEvidence.claim_id == Claim.id)
        .where(
            ResolvedClaimEvidence.resolved_claim_id == resolved["magnitude"].id,
            ResolvedClaimEvidence.stance == "supporting",
        )
    )

    assert resolved["magnitude"].version == 2
    assert supporting_release == second.source_release_id


def test_rejected_claim_blocks_resolution_without_reviving_it(
    session: Session, tmp_path: Path
) -> None:
    result = ingest(session, tmp_path)
    assert result.source_release_id is not None
    rejected = session.scalar(
        select(Claim).where(
            Claim.source_release_id == result.source_release_id,
            Claim.claim_type == "magnitude",
        )
    )
    assert rejected is not None
    rejected.assertion_status = ClaimAssertionStatus.REJECTED
    session.flush()

    with pytest.raises(ValueError, match="Non-reviewable claims block resolution"):
        accept_and_resolve_release(session, result.source_release_id)

    assert rejected.assertion_status == ClaimAssertionStatus.REJECTED
    assert session.scalar(select(func.count()).select_from(ResolvedClaim)) == 0


def test_revised_release_refreshes_event_projections_and_public_prose(
    session: Session, tmp_path: Path
) -> None:
    first = ingest(session, tmp_path)
    assert first.source_release_id is not None
    accept_and_resolve_release(session, first.source_release_id)
    revised_payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    revised_payload["features"][0]["properties"]["title"] = "Revised Alaska earthquake title"
    revised_payload["features"][0]["properties"]["mag"] = 9.1
    revised_payload["features"][0]["geometry"]["coordinates"] = [-148.0, 61.0, 30.0]
    revised_fixture = tmp_path / "revised.geojson"
    revised_fixture.write_text(json.dumps(revised_payload), encoding="utf-8")
    second = ingest_usgs(
        session,
        adapter=USGSEarthquakeAdapter(),
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
        fixture_path=revised_fixture,
    )
    assert second.source_release_id is not None
    accept_and_resolve_release(session, second.source_release_id)

    event = session.scalar(select(Event))
    assert event is not None
    assert event.canonical_title == "Revised Alaska earthquake title"
    assert session.scalar(select(func.count()).select_from(Event)) == 1
    location = session.scalar(select(EventLocation))
    assert location is not None and location.provenance_resolved_claim_id is not None

    profile = publish_golden_profile(
        session,
        store=LocalFilesystemPublishedProfileStore(tmp_path / "published"),
    )
    manifest = session.get(PublicationManifest, profile.publication_manifest_id)
    assert manifest is not None
    payload = LocalFilesystemPublishedProfileStore(tmp_path / "published").read(
        manifest.storage_uri, manifest.content_hash
    )
    magnitude_statement = payload["sections"]["recorded_on_this_date"][7]
    assert magnitude_statement["statement"] == "USGS reports a magnitude of 9.1 Mw."


def test_quality_grade_derivation_explains_single_source_consequence() -> None:
    grade, explanation, dimensions = derive_quality(
        independent_sources=1, complete_predicates=9
    )

    assert grade == "B"
    assert "one validated official USGS" in explanation
    assert dimensions["source_independence"] == "single authoritative source"
    assert len(dimensions) == 8

    incomplete_grade, incomplete_explanation, _ = derive_quality(
        independent_sources=2, complete_predicates=7
    )
    assert incomplete_grade == "C"
    assert incomplete_explanation.startswith("Grade C:")
    assert "7/9" in incomplete_explanation
    assert "2 independent sources" in incomplete_explanation


def test_golden_resolver_rejects_a_non_usgs_release(
    session: Session,
) -> None:
    other_source = Source(
        slug="not-usgs",
        name="A different source",
    )
    session.add(other_source)
    session.flush()
    release = create_source_release(
        session,
        source_id=other_source.id,
        release_label="not-usgs-v1",
        source_url="https://example.invalid/not-usgs",
        raw_storage_uri="test://not-usgs",
        raw_bytes=b"not USGS",
        raw_record_count=1,
    )

    with pytest.raises(ValueError, match="requires a USGS source release"):
        accept_and_resolve_release(session, release.id)

    assert session.scalar(select(func.count()).select_from(ResolvedClaim)) == 0


def test_missing_casualty_value_is_not_converted_to_zero(
    session: Session, tmp_path: Path
) -> None:
    _, profile = publish(session, tmp_path)
    manifest = session.get(PublicationManifest, profile.publication_manifest_id)
    assert manifest is not None
    payload = LocalFilesystemPublishedProfileStore(tmp_path / "published").read(
        manifest.storage_uri, manifest.content_hash
    )

    missing = payload["sections"]["evidence_notes"][0]["details"]["missing_data"]["casualties"]
    assert missing["state"] == "unavailable"
    assert "zero" in missing["reason"]
    assert "value" not in missing


def test_manifest_and_object_hashes_match_and_include_required_snapshot_metadata(
    session: Session, tmp_path: Path
) -> None:
    result, profile = publish(session, tmp_path)
    manifest = session.get(PublicationManifest, profile.publication_manifest_id)
    assert manifest is not None
    payload = LocalFilesystemPublishedProfileStore(tmp_path / "published").read(
        manifest.storage_uri, manifest.content_hash
    )

    assert manifest.content_hash == content_hash(payload)
    assert manifest.storage_uri == "day/1964-03-27/profile-v1.json"
    assert manifest.metadata_json["source_release_ids"] == [str(result.source_release_id)]
    assert manifest.metadata_json["resolved_claim_versions"]


def test_republish_creates_version_two_without_mutating_version_one(
    session: Session, tmp_path: Path
) -> None:
    _, first = publish(session, tmp_path)
    first_manifest = session.get(PublicationManifest, first.publication_manifest_id)
    assert first_manifest is not None
    original_hash = first_manifest.content_hash
    second = publish_golden_profile(
        session,
        store=LocalFilesystemPublishedProfileStore(tmp_path / "published"),
    )
    second_manifest = session.get(PublicationManifest, second.publication_manifest_id)

    assert second_manifest is not None and second_manifest.version == 2
    assert second_manifest.storage_uri == "day/1964-03-27/profile-v2.json"
    assert first_manifest.version == 1
    assert first_manifest.content_hash == original_hash
    assert second_manifest.supersedes_manifest_id == first_manifest.id


def test_api_returns_verified_golden_profile(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    publish(session, tmp_path)
    monkeypatch.setattr(main.settings, "published_profile_root", tmp_path / "published")
    main.app.dependency_overrides[get_session] = override_session(session)
    try:
        response = TestClient(main.app).get("/api/v1/day/1964-03-27")
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "published"
    assert body["profile"]["quality"]["grade"] == "B"
    assert body["profile"]["sections"]["recorded_on_this_date"][7]["details"]["value"] == 9.2


def test_api_distinguishes_invalid_outside_and_unpublished_dates(
    session: Session,
) -> None:
    main.app.dependency_overrides[get_session] = override_session(session)
    client = TestClient(main.app)
    try:
        invalid = client.get("/api/v1/day/not-a-date")
        outside = client.get("/api/v1/day/1899-12-31")
        unpublished = client.get("/api/v1/day/1964-03-28")
    finally:
        main.app.dependency_overrides.clear()

    assert invalid.status_code == 422
    assert outside.status_code == 404
    assert outside.json()["status"] == "date_out_of_supported_range"
    assert unpublished.status_code == 404
    assert unpublished.json()["status"] == "profile_not_published"


def test_corrupt_or_missing_publication_object_returns_integrity_failure(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, profile = publish(session, tmp_path)
    manifest = session.get(PublicationManifest, profile.publication_manifest_id)
    assert manifest is not None
    (tmp_path / "published" / manifest.storage_uri).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(main.settings, "published_profile_root", tmp_path / "published")
    main.app.dependency_overrides[get_session] = override_session(session)
    try:
        response = TestClient(main.app).get("/api/v1/day/1964-03-27")
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 503
    assert response.json()["status"] == "profile_storage_unavailable"


def test_failed_validation_records_failure_and_cannot_publish(
    session: Session, tmp_path: Path
) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one"):
        ingest_usgs(
            session,
            adapter=USGSEarthquakeAdapter(),
            raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
            fixture_path=invalid,
        )

    assert session.scalar(select(PipelineRun.status)) == "failed"
    assert session.scalar(select(QualityCheck.status)) == "failed"
    assert session.scalar(select(func.count()).select_from(SourceRelease)) == 0
    assert session.scalar(select(func.count()).select_from(PublicationManifest)) == 0
    with pytest.raises(ValueError, match="not been ingested"):
        publish_golden_profile(
            session,
            store=LocalFilesystemPublishedProfileStore(tmp_path / "published"),
        )


def test_acquisition_failure_records_failed_run_and_quality_check(
    session: Session, tmp_path: Path
) -> None:
    with pytest.raises(FileNotFoundError):
        ingest_usgs(
            session,
            adapter=USGSEarthquakeAdapter(),
            raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
            fixture_path=tmp_path / "missing.geojson",
        )

    assert session.scalar(select(PipelineRun.status)) == "failed"
    assert session.scalar(select(QualityCheck.status)) == "failed"
    assert session.scalar(select(func.count()).select_from(SourceRelease)) == 0


@pytest.mark.parametrize(
    "terminal_status",
    [ClaimAssertionStatus.RETRACTED, ClaimAssertionStatus.SUPERSEDED],
)
def test_terminal_nonaccepted_claim_states_block_resolution(
    session: Session,
    tmp_path: Path,
    terminal_status: ClaimAssertionStatus,
) -> None:
    result = ingest(session, tmp_path)
    assert result.source_release_id is not None
    claim = session.scalar(
        select(Claim).where(
            Claim.source_release_id == result.source_release_id,
            Claim.claim_type == "magnitude",
        )
    )
    assert claim is not None
    claim.assertion_status = terminal_status
    session.flush()

    with pytest.raises(ValueError, match="Non-reviewable claims block resolution"):
        accept_and_resolve_release(session, result.source_release_id)

    assert claim.assertion_status == terminal_status
    assert session.scalar(select(func.count()).select_from(ResolvedClaim)) == 0


def test_utc_and_local_date_statements_have_separate_provenance_roots(
    session: Session, tmp_path: Path
) -> None:
    _, profile = publish(session, tmp_path)
    manifest = session.get(PublicationManifest, profile.publication_manifest_id)
    assert manifest is not None
    payload = LocalFilesystemPublishedProfileStore(tmp_path / "published").read(
        manifest.storage_uri, manifest.content_hash
    )
    statements = payload["sections"]["recorded_on_this_date"]

    assert statements[3]["statement_id"] == "event-time-utc"
    assert statements[3]["provenance"]["supporting_claims"][0]["predicate"] == (
        "occurrence_timestamp"
    )
    assert statements[4]["statement_id"] == "event-local-civil-date"
    assert statements[4]["provenance"]["supporting_claims"][0]["predicate"] == (
        "local_civil_date"
    )


def test_identity_title_type_and_location_statements_have_atomic_provenance(
    session: Session, tmp_path: Path
) -> None:
    _, profile = publish(session, tmp_path)
    manifest = session.get(PublicationManifest, profile.publication_manifest_id)
    assert manifest is not None
    payload = LocalFilesystemPublishedProfileStore(tmp_path / "published").read(
        manifest.storage_uri, manifest.content_hash
    )
    statements = {
        statement["statement_id"]: statement
        for statement in payload["sections"]["recorded_on_this_date"]
    }

    expected_predicates = {
        "event-identity": "event_identity",
        "event-title": "event_title",
        "event-type": "event_type",
        "event-geography": "epicenter_geography",
        "event-coordinates": "epicenter_coordinates",
    }
    for statement_id, predicate in expected_predicates.items():
        assert statements[statement_id]["provenance"]["supporting_claims"][0][
            "predicate"
        ] == predicate


def test_acceptance_resolves_in_progress_review_tasks(
    session: Session, tmp_path: Path
) -> None:
    result = ingest(session, tmp_path)
    task = session.scalar(select(ReviewTask))
    assert task is not None
    task.status = "in_progress"
    session.flush()

    assert result.source_release_id is not None
    accept_and_resolve_release(session, result.source_release_id)

    assert task.status == "resolved"
    assert task.completed_at is not None


@pytest.mark.parametrize(
    "terminal_status",
    [ClaimAssertionStatus.RETRACTED, ClaimAssertionStatus.SUPERSEDED],
)
def test_admin_decision_cannot_revive_terminal_claims(
    session: Session,
    tmp_path: Path,
    terminal_status: ClaimAssertionStatus,
) -> None:
    result = ingest(session, tmp_path)
    claim = session.scalar(
        select(Claim).where(Claim.source_release_id == result.source_release_id)
    )
    assert claim is not None
    claim.assertion_status = terminal_status
    session.flush()
    main.app.dependency_overrides[get_session] = override_session(session)
    try:
        response = TestClient(main.app).post(
            f"/api/v1/admin/claims/{claim.id}/decision",
            headers={
                "X-Development-Review-Token": main.settings.development_review_token
            },
            json={"decision": "accepted"},
        )
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 409
    assert claim.assertion_status == terminal_status


def test_quality_assessment_is_published_with_grade_and_explanation(
    session: Session, tmp_path: Path
) -> None:
    publish(session, tmp_path)
    assessment = session.scalar(select(QualityAssessment))
    assert assessment is not None
    assert assessment.public_grade == "B"
    assert "single-source" in (assessment.public_explanation or "").lower()
    evidence = session.scalar(select(PublicationStatementEvidence))
    assert evidence is not None
    quality_snapshots = evidence.evidence_snapshot["evidence"][0]["claim"][
        "source_release"
    ]["release"]["quality_assessments"]
    assert quality_snapshots[0]["public_grade"] == "B"
    assert "single-source" in quality_snapshots[0]["public_explanation"].lower()


def test_rejecting_claim_closes_its_review_task(
    session: Session, tmp_path: Path
) -> None:
    result = ingest(session, tmp_path)
    claim = session.scalar(
        select(Claim).where(Claim.source_release_id == result.source_release_id)
    )
    assert claim is not None
    main.app.dependency_overrides[get_session] = override_session(session)
    try:
        response = TestClient(main.app).post(
            f"/api/v1/admin/claims/{claim.id}/decision",
            headers={
                "X-Development-Review-Token": main.settings.development_review_token
            },
            json={"decision": "rejected"},
        )
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    task = session.scalar(select(ReviewTask).where(ReviewTask.claim_id == claim.id))
    assert task is not None
    assert task.status == "resolved"
    assert task.completed_at is not None


def test_development_review_guard_is_explicit_and_blocks_unguarded_access(
    session: Session,
) -> None:
    main.app.dependency_overrides[get_session] = override_session(session)
    try:
        response = TestClient(main.app).get("/api/v1/admin/claims")
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 403
    assert "not production authentication" in response.json()["detail"]


def test_subsecond_usgs_timestamp_fails_before_release_creation(
    session: Session, tmp_path: Path
) -> None:
    fixture = tmp_path / "subsecond.geojson"
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["features"][0]["properties"]["time"] += 1
    fixture.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="subsecond precision"):
        ingest_usgs(
            session,
            adapter=USGSEarthquakeAdapter(),
            raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
            fixture_path=fixture,
        )

    assert session.scalar(select(PipelineRun.status)) == "failed"
    assert session.scalar(select(QualityCheck.status)) == "failed"
    assert session.scalar(select(func.count()).select_from(SourceRelease)) == 0


def test_canonical_event_type_comes_from_resolved_source_claim(
    session: Session, tmp_path: Path
) -> None:
    fixture = tmp_path / "retyped.geojson"
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["features"][0]["properties"]["type"] = "seismic-event"
    fixture.write_text(json.dumps(payload), encoding="utf-8")
    result = ingest_usgs(
        session,
        adapter=USGSEarthquakeAdapter(),
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
        fixture_path=fixture,
    )
    assert result.source_release_id is not None
    accept_and_resolve_release(session, result.source_release_id)

    event = session.scalar(select(Event))
    assert event is not None
    assert event.event_type == "seismic-event"
