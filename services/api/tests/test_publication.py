from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    ComparabilityStatus,
    DataStatus,
    DayProfile,
    DerivedValue,
    DerivedValueInput,
    Methodology,
    ProfileType,
    PublicationManifest,
    PublicationStatementEvidence,
    PublicationStatus,
    TemporalAssignment,
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
