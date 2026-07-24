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
from app.governance import ClaimReviewDecision, reviewed_resolutions_for_release
from app.models import (
    Claim,
    ClaimAssertionStatus,
    Event,
    EventLocation,
    EventTime,
    GeographyVersion,
    PipelineRun,
    PublicationManifest,
    QualityAssessment,
    QualityCheck,
    RawSourceRecord,
    ResolvedClaim,
    ResolvedClaimEvidence,
    ReviewTask,
    Source,
    SourceLineage,
    SourceLineageRelationship,
    SourceRelease,
)
from app.services import (
    LocalFilesystemPublishedProfileStore,
    content_hash,
    create_source_release,
)
from app.ucdp import ingest_ucdp_annual, review_ucdp_annual
from app.un_wpp import ingest_un_wpp, review_un_wpp
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
UN_WPP_FIXTURE = (
    ROOT / "data/fixtures/un-wpp/wpp2024-world-selected-years.csv"
)
UCDP_ANNUAL_FIXTURE = (
    ROOT / "data/fixtures/ucdp/ucdp-prio-26.1-conflicts-1964.csv"
)


def ingest(session: Session, tmp_path: Path, fixture_path: Path = FIXTURE):
    return ingest_usgs(
        session,
        adapter=USGSEarthquakeAdapter(),
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
        fixture_path=fixture_path,
    )


def publish(session: Session, tmp_path: Path, fixture_path: Path = FIXTURE):
    result = ingest(session, tmp_path, fixture_path)
    assert result.source_release_id is not None
    accept_and_resolve_release(session, result.source_release_id)
    un_result = ingest_un_wpp(
        session,
        fixture_path=UN_WPP_FIXTURE,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )
    review_un_wpp(session, un_result.source_release_id)
    ucdp_result = ingest_ucdp_annual(
        session,
        fixture_path=UCDP_ANNUAL_FIXTURE,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )
    review_ucdp_annual(session, ucdp_result.source_release_id)
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
    assert all(
        row.source_record_hash_sha256 == result.record_hash for row in claims.values()
    )


def test_dry_run_records_validation_without_importing_release_or_claims(
    session: Session, tmp_path: Path
) -> None:
    result = ingest_usgs(
        session,
        adapter=USGSEarthquakeAdapter(),
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
        fixture_path=FIXTURE,
        dry_run=True,
    )

    assert result.dry_run is True
    assert result.pipeline_run_id is not None
    assert result.source_release_id is None
    assert result.claim_ids == ()
    assert result.record_hash != result.checksum
    assert session.scalar(select(PipelineRun.status)) == "succeeded"
    assert session.scalar(select(QualityCheck.status)) == "passed"
    assert session.scalar(select(func.count()).select_from(SourceRelease)) == 0
    assert session.scalar(select(func.count()).select_from(Claim)) == 0


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


def evidence_releases(session: Session, count: int) -> list[SourceRelease]:
    source = Source(slug=f"resolution-source-{count}", name="Resolution test source")
    session.add(source)
    session.flush()
    return [
        create_source_release(
            session,
            source_id=source.id,
            release_label=f"release-{index}",
            source_url=f"https://example.invalid/release-{index}",
            raw_storage_uri=f"test://release-{index}",
            raw_bytes=f"release-{index}".encode(),
            raw_record_count=1,
        )
        for index in range(count)
    ]


def test_out_of_tolerance_dissent_remains_unresolved(session: Session) -> None:
    releases = evidence_releases(session, 3)
    decision = deterministic_resolution(
        session,
        (
            EvidenceCandidate(9.2, True, releases[0].id),
            EvidenceCandidate(9.2, False, releases[1].id),
            EvidenceCandidate(9.0, False, releases[2].id),
        ),
        tolerance=0.05,
    )

    assert decision.status == "unresolved"
    assert decision.supporting_indexes == (0, 1)
    assert decision.dissenting_indexes == (2,)


def test_dependent_lineage_is_not_counted_as_independent(session: Session) -> None:
    releases = evidence_releases(session, 3)
    session.add(
        SourceLineage(
            child_release_id=releases[1].id,
            parent_release_id=releases[0].id,
            relationship=SourceLineageRelationship.REPUBLISHED,
        )
    )
    session.flush()
    decision = deterministic_resolution(
        session,
        (
            EvidenceCandidate("same", True, releases[0].id),
            EvidenceCandidate("same", False, releases[1].id),
            EvidenceCandidate("same", False, releases[2].id),
        )
    )

    assert decision.independent_source_count == 2


def test_unbounded_disagreement_remains_unresolved(session: Session) -> None:
    releases = evidence_releases(session, 2)
    decision = deterministic_resolution(
        session,
        (
            EvidenceCandidate("A", True, releases[0].id),
            EvidenceCandidate("B", False, releases[1].id),
        )
    )
    assert decision.status == "unresolved"
    assert decision.dissenting_indexes == (1,)


def test_quality_explanation_matches_a_lower_grade() -> None:
    grade, explanation, _ = derive_quality(
        independent_sources=1, complete_predicates=8
    )

    assert grade == "C"
    assert explanation.startswith("Grade C:")


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


def test_new_usgs_release_attaches_every_current_claim_to_a_current_resolution(
    session: Session, tmp_path: Path
) -> None:
    first = ingest(session, tmp_path)
    assert first.source_release_id is not None
    accept_and_resolve_release(session, first.source_release_id)

    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["features"][0]["properties"]["mag"] = 9.1
    revised_fixture = tmp_path / "revised-usgs.geojson"
    revised_fixture.write_text(json.dumps(payload), encoding="utf-8")
    second = ingest(session, tmp_path, revised_fixture)
    assert second.source_release_id is not None
    accept_and_resolve_release(session, second.source_release_id)

    current_claims = list(
        session.scalars(
            select(Claim).where(
                Claim.source_release_id == second.source_release_id
            )
        )
    )
    current_resolutions = reviewed_resolutions_for_release(
        session, second.source_release_id
    )
    assert len(current_resolutions) == 9
    for claim in current_claims:
        assert session.scalar(
            select(ResolvedClaimEvidence.claim_id).where(
                ResolvedClaimEvidence.resolved_claim_id
                == current_resolutions[claim.claim_type].id,
                ResolvedClaimEvidence.claim_id == claim.id,
                ResolvedClaimEvidence.stance == "supporting",
            )
        ) == claim.id
    assert session.scalar(select(func.count()).select_from(Event)) == 1


def test_usgs_public_statements_are_derived_from_reviewed_values(
    session: Session, tmp_path: Path
) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    feature = payload["features"][0]
    feature["properties"]["mag"] = 9.1
    feature["properties"]["title"] = "M 9.1 - Revised Alaska Earthquake"
    feature["properties"]["place"] = "Revised Alaska Earthquake"
    feature["properties"]["time"] += 1000
    feature["geometry"]["coordinates"] = [-147.5, 60.8, 30]
    revised_fixture = tmp_path / "revised-usgs.geojson"
    revised_fixture.write_text(json.dumps(payload), encoding="utf-8")

    _, profile = publish(session, tmp_path, revised_fixture)
    manifest = session.get(PublicationManifest, profile.publication_manifest_id)
    assert manifest is not None
    artifact = LocalFilesystemPublishedProfileStore(
        tmp_path / "published"
    ).read(manifest.storage_uri, manifest.content_hash)
    statements = {
        item["statement_id"]: item["statement"]
        for item in artifact["sections"]["recorded_on_this_date"]
    }

    assert statements["event-title"] == "M 9.1 - Revised Alaska Earthquake"
    assert statements["event-time-utc"].endswith("03:36:17 UTC.")
    assert statements["event-magnitude"] == "USGS reports a magnitude of 9.1 MW."
    assert statements["event-depth"] == "USGS reports a depth of 30 km."
    assert statements["event-geography"] == (
        "USGS names the location as Revised Alaska Earthquake."
    )
    assert statements["event-coordinates"] == (
        "USGS places the epicenter at 60.8 latitude, -147.5 longitude."
    )


def test_quality_grade_derivation_explains_single_source_consequence() -> None:
    grade, explanation, dimensions = derive_quality(
        independent_sources=1, complete_predicates=9
    )

    assert grade == "B"
    assert "one validated official USGS" in explanation
    assert dimensions["source_independence"] == "single authoritative source"
    assert len(dimensions) == 8


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
    assert manifest.metadata_json["source_release_ids"][0] == str(
        result.source_release_id
    )
    assert len(manifest.metadata_json["source_release_ids"]) == 3
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
    assert body["profile"]["sections"]["recorded_on_this_date"][3]["details"]["value"] == 9.2


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


def test_quality_assessment_is_published_with_grade_and_explanation(
    session: Session, tmp_path: Path
) -> None:
    publish(session, tmp_path)
    assessment = session.scalar(select(QualityAssessment))
    assert assessment is not None
    assert assessment.public_grade == "B"
    assert "single-source" in (assessment.public_explanation or "").lower()


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


def test_admin_decision_records_ledger_and_resolution_requires_prior_acceptance(
    session: Session, tmp_path: Path
) -> None:
    result = ingest(session, tmp_path)
    assert result.source_release_id is not None
    claim = session.scalar(select(Claim).order_by(Claim.claim_type))
    assert claim is not None
    main.app.dependency_overrides[get_session] = override_session(session)
    headers = {
        "X-Development-Review-Token": main.settings.development_review_token
    }
    try:
        blocked = TestClient(main.app).post(
            f"/api/v1/admin/releases/{result.source_release_id}/resolve",
            headers=headers,
        )
        assert blocked.status_code == 400
        assert "explicitly accepted" in blocked.json()["detail"]

        blank = TestClient(main.app).post(
            f"/api/v1/admin/claims/{claim.id}/decision",
            headers=headers,
            json={"decision": "accepted", "rationale": "   "},
        )
        assert blank.status_code == 422
        session.refresh(claim)
        assert claim.assertion_status == ClaimAssertionStatus.CANDIDATE

        response = TestClient(main.app).post(
            f"/api/v1/admin/claims/{claim.id}/decision",
            headers=headers,
            json={
                "decision": "accepted",
                "rationale": "Matched the claim to the committed official fixture.",
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "accepted"
        repeated = TestClient(main.app).post(
            f"/api/v1/admin/claims/{claim.id}/decision",
            headers=headers,
            json={
                "decision": "accepted",
                "rationale": "Retried after the page became stale.",
            },
        )
        assert repeated.status_code == 409
        assert "candidate or in-review" in repeated.json()["detail"]
        decision = session.scalar(
            select(ClaimReviewDecision).where(
                ClaimReviewDecision.claim_id == claim.id
            )
        )
        assert decision is not None
        assert decision.rationale.startswith("Matched the claim")
    finally:
        main.app.dependency_overrides.clear()


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
