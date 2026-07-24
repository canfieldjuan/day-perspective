from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Claim,
    ComparabilityStatus,
    DataStatus,
    DateRole,
    DayProfile,
    DerivedValue,
    DerivedValueInput,
    Geography,
    GeographyVersion,
    LegalReviewStatus,
    Methodology,
    Metric,
    Observation,
    PipelineRun,
    ProfileType,
    PublicationManifest,
    PublicationStatementEvidence,
    PublicationStatus,
    QualityAssessment,
    Source,
    SourceLineage,
    SourceLineageRelationship,
    SourceRelease,
    TemporalAssignment,
    TemporalPrecision,
)
from app.services import (
    LocalFilesystemPublishedProfileStore,
    PublicationStatementEvidenceInput,
    content_hash,
    create_claim,
    publish_day_profile,
    record_correction,
    resolve_claim,
)
from tests.helpers import source_release


def payload(statement: str) -> dict[str, object]:
    return {
        "schema_version": "1",
        "date": "1969-07-20",
        "profile_type": "standard_statistical",
        "sections": {"evidence_notes": [{"statement_id": "test-only", "statement": statement}]},
    }


def statement_evidence(session: Session) -> list[PublicationStatementEvidenceInput]:
    release = source_release(session)
    claim = create_claim(
        session,
        source_release_id=release.id,
        source_record_locator="record:publication-statement",
        claim_type="synthetic_assertion",
        assertion_text="Synthetic provenance for publication testing.",
    )
    resolved = resolve_claim(
        session,
        canonical_key="test:publication-statement",
        resolved_value={"statement": "Synthetic publication provenance."},
        rationale="Test-only publication provenance.",
        supporting_claim_ids=[claim.id],
    )
    return [
        PublicationStatementEvidenceInput(
            statement_path="/sections/evidence_notes/0",
            resolved_claim_id=resolved.id,
        )
    ]


def untraceable_derived_value(session: Session) -> DerivedValue:
    methodology = Methodology(
        slug="test-derived-methodology",
        version="1",
        name="Test-only derived methodology",
        description="Creates an intentionally untraceable derived value for guard tests.",
        code_version="test",
        definition_hash="d" * 64,
    )
    session.add(methodology)
    session.flush()
    value = DerivedValue(
        methodology_id=methodology.id,
        value_kind="test-derived-value",
        period_start=date(1969, 7, 20),
        temporal_assignment=TemporalAssignment.UNIFORM_PERIOD_ALLOCATION,
        value_numeric=Decimal("1"),
        data_status=DataStatus.FINAL,
        comparability_status=ComparabilityStatus.UNKNOWN,
        input_fingerprint="e" * 64,
        calculation_version="test",
    )
    session.add(value)
    session.flush()
    return value


@pytest.mark.integration
def test_publication_manifest_hash_is_canonical_and_input_sensitive() -> None:
    first = payload("First")
    reordered = {"sections": first["sections"], "profile_type": first["profile_type"], "date": first["date"], "schema_version": "1"}
    assert content_hash(first) == content_hash(reordered)
    assert content_hash(first) != content_hash(payload("Changed"))


@pytest.mark.integration
def test_publication_snapshots_resolved_evidence_and_derives_manifest_hash(
    session: Session, tmp_path: Path
) -> None:
    release = source_release(session)
    supporting = create_claim(
        session,
        source_release_id=release.id,
        source_record_locator="record:snapshot-support",
        claim_type="synthetic_assertion",
        assertion_text="Original supporting assertion.",
    )
    dissenting = create_claim(
        session,
        source_release_id=release.id,
        source_record_locator="record:snapshot-dissent",
        claim_type="synthetic_assertion",
        assertion_text="Original dissenting assertion.",
    )
    resolved = resolve_claim(
        session,
        canonical_key="test:snapshot-resolution",
        resolved_value={"statement": "Original resolved value."},
        rationale="Snapshot test resolution.",
        supporting_claim_ids=[supporting.id],
        dissenting_claim_ids=[dissenting.id],
    )
    profile = publish_day_profile(
        session,
        store=LocalFilesystemPublishedProfileStore(tmp_path),
        profile_date=date(1969, 7, 20),
        profile_type=ProfileType.STANDARD_STATISTICAL,
        payload=payload("Snapshot test profile."),
        statement_evidence=[
            PublicationStatementEvidenceInput(
                statement_path="/sections/evidence_notes/0",
                resolved_claim_id=resolved.id,
            )
        ],
    )
    session.commit()
    evidence = session.scalar(
        select(PublicationStatementEvidence).where(
            PublicationStatementEvidence.publication_manifest_id
            == profile.publication_manifest_id
        )
    )
    manifest = session.get(PublicationManifest, profile.publication_manifest_id)
    assert evidence is not None and manifest is not None
    original_snapshot = evidence.evidence_snapshot
    original_snapshot_hash = evidence.evidence_snapshot_hash
    assert original_snapshot_hash == content_hash(original_snapshot)
    assert [item["stance"] for item in original_snapshot["evidence"]] == [
        "supporting",
        "dissenting",
    ]
    assert (
        original_snapshot["evidence"][0]["claim"]["source_release"]["release"][
            "raw_checksum_sha256"
        ]
        == release.raw_checksum_sha256
    )
    expected_source_hash = content_hash(
        {
            "schema_version": "1",
            "statements": [
                {
                    "statement_path": "/sections/evidence_notes/0",
                    "evidence_snapshot_hash": original_snapshot_hash,
                }
            ],
        }
    )
    assert manifest.source_snapshot_hash == expected_source_hash

    supporting.assertion_text = "Later working-graph edit."
    resolved.resolved_value = {"statement": "Later resolved working value."}
    session.commit()
    session.refresh(evidence)
    assert evidence.evidence_snapshot == original_snapshot
    assert evidence.evidence_snapshot_hash == original_snapshot_hash


@pytest.mark.integration
def test_publication_snapshots_derived_value_lineage(session: Session, tmp_path: Path) -> None:
    provenance = statement_evidence(session)
    resolved_claim_id = provenance[0].resolved_claim_id
    assert resolved_claim_id is not None
    derived_value = untraceable_derived_value(session)
    session.add(
        DerivedValueInput(
            derived_value_id=derived_value.id,
            resolved_claim_id=resolved_claim_id,
            input_role="primary",
        )
    )
    profile = publish_day_profile(
        session,
        store=LocalFilesystemPublishedProfileStore(tmp_path),
        profile_date=date(1969, 7, 20),
        profile_type=ProfileType.STANDARD_STATISTICAL,
        payload=payload("Derived snapshot profile."),
        statement_evidence=[
            PublicationStatementEvidenceInput(
                statement_path="/sections/evidence_notes/0",
                derived_value_id=derived_value.id,
            )
        ],
    )
    session.commit()
    evidence = session.scalar(
        select(PublicationStatementEvidence).where(
            PublicationStatementEvidence.publication_manifest_id
            == profile.publication_manifest_id
        )
    )
    assert evidence is not None
    assert evidence.evidence_snapshot["root_type"] == "derived_value"
    assert evidence.evidence_snapshot["inputs"][0]["root"]["root_type"] == "resolved_claim"
    assert evidence.evidence_snapshot_hash == content_hash(evidence.evidence_snapshot)


@pytest.mark.integration
def test_publication_snapshot_closes_over_mutable_transitive_dependencies(
    session: Session, tmp_path: Path
) -> None:
    pipeline = PipelineRun(
        pipeline_name="test-transitive-pipeline",
        code_version="test-code-v1",
        configuration_hash="1" * 64,
        status="succeeded",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        completed_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        details={"fixture_mode": True},
    )
    methodology = Methodology(
        slug="test-transitive-methodology",
        version="1",
        name="Test transitive methodology",
        description="Defines the test-only transitive publication value.",
        method_kind="calculation",
        formula="input",
        code_version="test-code-v1",
        definition_hash="2" * 64,
    )
    parent_source = Source(
        slug="test-parent-source",
        name="Test parent source",
        publisher="Test suite",
        canonical_url="https://example.invalid/parent",
        legal_review_status=LegalReviewStatus.NOT_REQUIRED,
    )
    child_source = Source(
        slug="test-child-source",
        name="Test child source",
        publisher="Test suite",
        canonical_url="https://example.invalid/child",
        legal_review_status=LegalReviewStatus.NOT_REQUIRED,
    )
    session.add_all([pipeline, methodology, parent_source, child_source])
    session.flush()
    parent_release = SourceRelease(
        source_id=parent_source.id,
        release_label="parent-v1",
        source_url="https://example.invalid/parent/v1",
        raw_storage_uri="test://raw/parent-v1",
        raw_checksum_sha256="3" * 64,
        raw_record_count=1,
        pipeline_run_id=pipeline.id,
        legal_review_status=LegalReviewStatus.NOT_REQUIRED,
    )
    child_release = SourceRelease(
        source_id=child_source.id,
        release_label="child-v1",
        source_url="https://example.invalid/child/v1",
        raw_storage_uri="test://raw/child-v1",
        raw_checksum_sha256="4" * 64,
        raw_record_count=1,
        pipeline_run_id=pipeline.id,
        legal_review_status=LegalReviewStatus.NOT_REQUIRED,
    )
    session.add_all([parent_release, child_release])
    session.flush()
    session.add(
        SourceLineage(
            child_release_id=child_release.id,
            parent_release_id=parent_release.id,
            relationship=SourceLineageRelationship.DERIVED,
            methodology_id=methodology.id,
            note="Test-only derived lineage.",
        )
    )
    claim = Claim(
        source_release_id=child_release.id,
        source_record_locator="record:transitive",
        claim_type="synthetic_assertion",
        assertion_text="Test-only transitive assertion.",
        pipeline_run_id=pipeline.id,
    )
    session.add(claim)
    session.flush()
    resolved = resolve_claim(
        session,
        canonical_key="test:transitive-resolution",
        resolved_value={"statement": "Test transitive resolution."},
        rationale="Test-only transitive dependency resolution.",
        supporting_claim_ids=[claim.id],
    )
    geography = Geography(
        stable_key="test-transitive-geography",
        geography_kind="synthetic",
    )
    session.add(geography)
    session.flush()
    geography_version = GeographyVersion(
        geography_id=geography.id,
        provenance_resolved_claim_id=resolved.id,
        name="Test historical geography",
        identifier_code="TEST-1969",
        valid_from=date(1960, 1, 1),
        valid_to=date(1970, 12, 31),
        boundary_geometry=WKTElement(
            "MULTIPOLYGON(((-150 60,-149 60,-149 61,-150 61,-150 60)))",
            srid=4326,
        ),
    )
    metric = Metric(
        metric_key="test-transitive-metric",
        display_name="Test transitive metric",
        unit="test-unit",
        definition="The complete meaning of the test-only value.",
        provenance_resolved_claim_id=resolved.id,
        methodology_id=methodology.id,
    )
    session.add_all([geography_version, metric])
    session.flush()
    observation = Observation(
        metric_id=metric.id,
        geography_version_id=geography_version.id,
        source_release_id=child_release.id,
        provenance_resolved_claim_id=resolved.id,
        period_start=date(1969, 7, 20),
        temporal_precision=TemporalPrecision.DAY,
        temporal_assignment=TemporalAssignment.DIRECT_RECORD,
        date_role=DateRole.OCCURRED,
        value_numeric=Decimal("7"),
        data_status=DataStatus.FINAL,
    )
    session.add(observation)
    session.flush()
    derived = DerivedValue(
        metric_id=metric.id,
        geography_version_id=geography_version.id,
        methodology_id=methodology.id,
        value_kind="test-derived-value",
        period_start=date(1969, 7, 20),
        temporal_assignment=TemporalAssignment.UNIFORM_PERIOD_ALLOCATION,
        value_numeric=Decimal("7"),
        data_status=DataStatus.FINAL,
        comparability_status=ComparabilityStatus.COMPARABLE,
        input_fingerprint="5" * 64,
        calculation_version="test-code-v1",
    )
    session.add(derived)
    session.flush()
    session.add(
        DerivedValueInput(
            derived_value_id=derived.id,
            observation_id=observation.id,
            input_role="primary",
        )
    )
    session.add_all(
        [
            QualityAssessment(
                source_release_id=child_release.id,
                methodology_id=methodology.id,
                assessment_kind="release_quality",
                score=Decimal("0.80"),
                findings={"note": "release assessment"},
            ),
            QualityAssessment(
                claim_id=claim.id,
                methodology_id=methodology.id,
                assessment_kind="claim_quality",
                score=Decimal("0.81"),
                findings={"note": "claim assessment"},
            ),
            QualityAssessment(
                observation_id=observation.id,
                methodology_id=methodology.id,
                assessment_kind="observation_quality",
                score=Decimal("0.82"),
                findings={"note": "observation assessment"},
            ),
            QualityAssessment(
                derived_value_id=derived.id,
                methodology_id=methodology.id,
                assessment_kind="derived_quality",
                score=Decimal("0.83"),
                findings={"note": "derived assessment"},
            ),
            QualityAssessment(
                target_methodology_id=methodology.id,
                assessment_kind="methodology_quality",
                score=Decimal("0.84"),
                findings={"note": "methodology assessment"},
            ),
        ]
    )
    session.flush()
    profile = publish_day_profile(
        session,
        store=LocalFilesystemPublishedProfileStore(tmp_path),
        profile_date=date(1969, 7, 20),
        profile_type=ProfileType.STANDARD_STATISTICAL,
        payload=payload("Transitive dependency snapshot profile."),
        statement_evidence=[
            PublicationStatementEvidenceInput(
                statement_path="/sections/evidence_notes/0",
                derived_value_id=derived.id,
            )
        ],
    )
    session.commit()
    evidence = session.scalar(
        select(PublicationStatementEvidence).where(
            PublicationStatementEvidence.publication_manifest_id
            == profile.publication_manifest_id
        )
    )
    assert evidence is not None
    snapshot = evidence.evidence_snapshot
    derived_snapshot = snapshot["derived_value"]
    assert derived_snapshot["metric"]["metric_key"] == "test-transitive-metric"
    assert derived_snapshot["metric"]["unit"] == "test-unit"
    geography_snapshot = derived_snapshot["geography_version"]
    assert geography_snapshot["name"] == "Test historical geography"
    assert geography_snapshot["boundary_geojson"]["type"] == "MultiPolygon"
    observation_snapshot = snapshot["inputs"][0]["root"]["observation"]
    release_snapshot = observation_snapshot["source_release"]["release"]
    assert release_snapshot["pipeline_run"]["configuration_hash"] == "1" * 64
    assert release_snapshot["lineage"][0]["parent_release"]["release"][
        "raw_checksum_sha256"
    ] == "3" * 64
    assert {
        item["assessment_kind"]
        for item in derived_snapshot["quality_assessments"]
    } == {"derived_quality"}
    assert {
        item["assessment_kind"]
        for item in observation_snapshot["quality_assessments"]
    } == {"observation_quality"}
    claim_snapshot = derived_snapshot["metric"]["provenance_resolved_claim"]["evidence"][0][
        "claim"
    ]
    assert {
        item["assessment_kind"] for item in claim_snapshot["quality_assessments"]
    } == {"claim_quality"}
    assert {
        item["assessment_kind"] for item in release_snapshot["quality_assessments"]
    } == {"release_quality"}
    assert {
        item["assessment_kind"]
        for item in derived_snapshot["methodology"]["quality_assessments"]
    } == {"methodology_quality"}
    assert evidence.evidence_snapshot_hash == content_hash(snapshot)


@pytest.mark.integration
def test_published_profile_is_immutable(session: Session, tmp_path: Path) -> None:
    profile = publish_day_profile(
        session,
        store=LocalFilesystemPublishedProfileStore(tmp_path),
        profile_date=date(1969, 7, 20),
        profile_type=ProfileType.STANDARD_STATISTICAL,
        payload=payload("Immutable test profile."),
        statement_evidence=statement_evidence(session),
    )
    session.commit()
    with pytest.raises(DBAPIError):
        session.execute(
            update(PublicationManifest)
            .where(PublicationManifest.id == profile.publication_manifest_id)
            .values(storage_uri="forbidden://change")
        )
        session.commit()
    session.rollback()
    with pytest.raises(DBAPIError):
        session.execute(update(DayProfile).where(DayProfile.id == profile.id).values(content_hash="b" * 64))
        session.commit()
    session.rollback()


@pytest.mark.integration
def test_published_statement_evidence_cannot_move_to_a_draft_manifest(
    session: Session, tmp_path: Path
) -> None:
    profile = publish_day_profile(
        session,
        store=LocalFilesystemPublishedProfileStore(tmp_path),
        profile_date=date(1969, 7, 20),
        profile_type=ProfileType.STANDARD_STATISTICAL,
        payload=payload("Evidence immutability test profile."),
        statement_evidence=statement_evidence(session),
    )
    session.commit()
    draft = PublicationManifest(
        profile_date=date(1969, 7, 20),
        profile_type=ProfileType.STANDARD_STATISTICAL,
        version=2,
        status=PublicationStatus.DRAFT,
        content_hash="c" * 64,
        source_snapshot_hash="a" * 64,
        storage_uri="pending://test-draft",
        code_version="test",
    )
    session.add(draft)
    session.flush()
    evidence = session.scalar(
        select(PublicationStatementEvidence).where(
            PublicationStatementEvidence.publication_manifest_id == profile.publication_manifest_id
        )
    )
    assert evidence is not None
    with pytest.raises(DBAPIError):
        session.execute(
            update(PublicationStatementEvidence)
            .where(PublicationStatementEvidence.id == evidence.id)
            .values(publication_manifest_id=draft.id)
        )
        session.commit()
    session.rollback()


@pytest.mark.integration
def test_day_profile_hash_must_match_its_published_manifest(session: Session) -> None:
    manifest = PublicationManifest(
        profile_date=date(1969, 7, 20),
        profile_type=ProfileType.STANDARD_STATISTICAL,
        version=1,
        status=PublicationStatus.PUBLISHED,
        content_hash="a" * 64,
        source_snapshot_hash="b" * 64,
        storage_uri="test://published-manifest",
        code_version="test",
        published_at=datetime.now(UTC),
    )
    session.add(manifest)
    session.flush()
    session.add(
        DayProfile(
            profile_date=date(1969, 7, 20),
            profile_type=ProfileType.STANDARD_STATISTICAL,
            publication_manifest_id=manifest.id,
            content_hash="c" * 64,
        )
    )
    with pytest.raises(DBAPIError):
        session.commit()
    session.rollback()


@pytest.mark.integration
def test_correction_creates_a_new_version_without_overwriting_original(session: Session, tmp_path: Path) -> None:
    store = LocalFilesystemPublishedProfileStore(tmp_path)
    provenance = statement_evidence(session)
    original = publish_day_profile(
        session,
        store=store,
        profile_date=date(1969, 7, 20),
        profile_type=ProfileType.STANDARD_STATISTICAL,
        payload=payload("Original test profile."),
        statement_evidence=provenance,
    )
    session.commit()
    original_manifest = session.get(PublicationManifest, original.publication_manifest_id)
    assert original_manifest is not None
    original_hash = original_manifest.content_hash
    replacement = publish_day_profile(
        session,
        store=store,
        profile_date=date(1969, 7, 20),
        profile_type=ProfileType.STANDARD_STATISTICAL,
        payload=payload("Corrected test profile."),
        statement_evidence=provenance,
        supersedes_manifest_id=original_manifest.id,
        supersedes_day_profile_id=original.id,
    )
    record_correction(
        session,
        original_manifest_id=original_manifest.id,
        replacement_manifest_id=replacement.publication_manifest_id,
        rationale="Synthetic correction verifies append-only behavior.",
    )
    session.commit()
    replacement_manifest = session.get(PublicationManifest, replacement.publication_manifest_id)
    assert replacement_manifest is not None
    assert replacement_manifest.version == original_manifest.version + 1
    assert replacement_manifest.supersedes_manifest_id == original_manifest.id
    assert original_manifest.content_hash == original_hash


@pytest.mark.integration
def test_publishing_requires_a_provenance_mapping_for_each_statement(
    session: Session, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="Every published statement requires"):
        publish_day_profile(
            session,
            store=LocalFilesystemPublishedProfileStore(tmp_path),
            profile_date=date(1969, 7, 20),
            profile_type=ProfileType.STANDARD_STATISTICAL,
            payload=payload("Unmapped test profile."),
            statement_evidence=[],
        )


@pytest.mark.integration
def test_publishing_rejects_an_untraceable_derived_statement(session: Session, tmp_path: Path) -> None:
    derived_value = untraceable_derived_value(session)
    with pytest.raises(ValueError, match="traceable"):
        publish_day_profile(
            session,
            store=LocalFilesystemPublishedProfileStore(tmp_path),
            profile_date=date(1969, 7, 20),
            profile_type=ProfileType.STANDARD_STATISTICAL,
            payload=payload("Untraceable derived statement."),
            statement_evidence=[
                PublicationStatementEvidenceInput(
                    statement_path="/sections/evidence_notes/0",
                    derived_value_id=derived_value.id,
                )
            ],
        )


@pytest.mark.integration
def test_untraceable_derived_value_cannot_commit(session: Session) -> None:
    untraceable_derived_value(session)
    with pytest.raises(DBAPIError):
        session.commit()
    session.rollback()


@pytest.mark.integration
def test_derived_value_input_cannot_move_from_its_only_parent(session: Session) -> None:
    provenance = statement_evidence(session)
    resolved_claim_id = provenance[0].resolved_claim_id
    assert resolved_claim_id is not None
    first = untraceable_derived_value(session)
    second = DerivedValue(
        methodology_id=first.methodology_id,
        value_kind="test-derived-value-second",
        period_start=date(1969, 7, 20),
        temporal_assignment=TemporalAssignment.UNIFORM_PERIOD_ALLOCATION,
        value_numeric=Decimal("2"),
        data_status=DataStatus.FINAL,
        comparability_status=ComparabilityStatus.UNKNOWN,
        input_fingerprint="f" * 64,
        calculation_version="test",
    )
    session.add(second)
    session.flush()
    first_input = DerivedValueInput(
        derived_value_id=first.id,
        resolved_claim_id=resolved_claim_id,
        input_role="primary",
    )
    session.add_all(
        [
            first_input,
            DerivedValueInput(
                derived_value_id=second.id,
                resolved_claim_id=resolved_claim_id,
                input_role="primary",
            ),
        ]
    )
    session.commit()
    with pytest.raises(DBAPIError, match="derived values require"):
        session.execute(
            update(DerivedValueInput)
            .where(DerivedValueInput.id == first_input.id)
            .values(derived_value_id=second.id)
        )
        session.commit()
    session.rollback()


@pytest.mark.integration
def test_derived_value_input_cannot_be_deleted_from_its_only_parent(session: Session) -> None:
    provenance = statement_evidence(session)
    resolved_claim_id = provenance[0].resolved_claim_id
    assert resolved_claim_id is not None
    derived_value = untraceable_derived_value(session)
    input_row = DerivedValueInput(
        derived_value_id=derived_value.id,
        resolved_claim_id=resolved_claim_id,
        input_role="primary",
    )
    session.add(input_row)
    session.commit()
    session.delete(input_row)
    with pytest.raises(DBAPIError, match="derived values require"):
        session.commit()
    session.rollback()


@pytest.mark.integration
def test_manifest_supersession_rejects_a_different_profile_date(session: Session, tmp_path: Path) -> None:
    original = publish_day_profile(
        session,
        store=LocalFilesystemPublishedProfileStore(tmp_path),
        profile_date=date(1969, 7, 20),
        profile_type=ProfileType.STANDARD_STATISTICAL,
        payload=payload("Original manifest identity test."),
        statement_evidence=statement_evidence(session),
    )
    session.commit()
    session.add(
        PublicationManifest(
            profile_date=date(1970, 7, 20),
            profile_type=ProfileType.STANDARD_STATISTICAL,
            version=1,
            status=PublicationStatus.DRAFT,
            content_hash="c" * 64,
            source_snapshot_hash="a" * 64,
            storage_uri="pending://invalid-supersession",
            code_version="test",
            supersedes_manifest_id=original.publication_manifest_id,
        )
    )
    with pytest.raises(DBAPIError, match="same date and profile type"):
        session.commit()
    session.rollback()


@pytest.mark.integration
def test_publishing_rejects_a_second_successor_for_the_same_manifest(
    session: Session, tmp_path: Path
) -> None:
    provenance = statement_evidence(session)
    original = publish_day_profile(
        session,
        store=LocalFilesystemPublishedProfileStore(tmp_path),
        profile_date=date(1969, 7, 20),
        profile_type=ProfileType.STANDARD_STATISTICAL,
        payload=payload("Original linear-history profile."),
        statement_evidence=provenance,
    )
    session.commit()
    publish_day_profile(
        session,
        store=LocalFilesystemPublishedProfileStore(tmp_path),
        profile_date=date(1969, 7, 20),
        profile_type=ProfileType.STANDARD_STATISTICAL,
        payload=payload("First successor profile."),
        statement_evidence=provenance,
        supersedes_manifest_id=original.publication_manifest_id,
        supersedes_day_profile_id=original.id,
    )
    session.commit()
    with pytest.raises(IntegrityError):
        publish_day_profile(
            session,
            store=LocalFilesystemPublishedProfileStore(tmp_path),
            profile_date=date(1969, 7, 20),
            profile_type=ProfileType.STANDARD_STATISTICAL,
            payload=payload("Invalid second successor profile."),
            statement_evidence=provenance,
            supersedes_manifest_id=original.publication_manifest_id,
            supersedes_day_profile_id=original.id,
        )
    session.rollback()


@pytest.mark.integration
def test_publishing_rejects_cross_date_profile_supersession(session: Session, tmp_path: Path) -> None:
    provenance = statement_evidence(session)
    original = publish_day_profile(
        session,
        store=LocalFilesystemPublishedProfileStore(tmp_path),
        profile_date=date(1969, 7, 20),
        profile_type=ProfileType.STANDARD_STATISTICAL,
        payload=payload("Original test profile."),
        statement_evidence=provenance,
    )
    session.commit()
    replacement_payload = payload("Invalid cross-date replacement.")
    replacement_payload["date"] = "1970-07-20"
    with pytest.raises(ValueError, match="same date and profile type"):
        publish_day_profile(
            session,
            store=LocalFilesystemPublishedProfileStore(tmp_path),
            profile_date=date(1970, 7, 20),
            profile_type=ProfileType.STANDARD_STATISTICAL,
            payload=replacement_payload,
            statement_evidence=provenance,
            supersedes_manifest_id=original.publication_manifest_id,
            supersedes_day_profile_id=original.id,
        )


def test_local_profile_store_refuses_to_replace_an_existing_artifact(tmp_path: Path) -> None:
    store = LocalFilesystemPublishedProfileStore(tmp_path)
    target_payload = payload("Target profile.")
    digest = content_hash(target_payload)
    destination = (
        tmp_path
        / ProfileType.STANDARD_STATISTICAL.value
        / f"1969-07-20-{digest[:16]}.json"
    )
    destination.parent.mkdir(parents=True)
    destination.write_text('{"unexpected":"existing artifact"}', encoding="utf-8")

    with pytest.raises(RuntimeError, match="manifest hash"):
        store.write(date(1969, 7, 20), ProfileType.STANDARD_STATISTICAL, target_payload)
