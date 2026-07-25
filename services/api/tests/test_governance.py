from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.governance import (
    EditorialSelection,
    EditorialSelectionStatus,
    SourceReleaseLicense,
    lineage_root_ids,
    record_editorial_selection,
)
from app.models import (
    DerivedValue,
    PublicationManifest,
    PublicationStatementEvidence,
    QualityCheck,
    ResolvedClaim,
    Source,
    SourceLineage,
    SourceLineageRelationship,
)
from app.services import LocalFilesystemPublishedProfileStore, create_source_release
from app.ucdp import ingest_ucdp_annual, review_ucdp_annual
from app.un_wpp import ingest_un_wpp, review_un_wpp
from app.usgs import (
    LocalFilesystemRawSourceStore,
    USGSEarthquakeAdapter,
    accept_and_resolve_release,
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


def ingest(session: Session, tmp_path: Path):
    return ingest_usgs(
        session,
        adapter=USGSEarthquakeAdapter(),
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
        fixture_path=FIXTURE,
    )


def review_context(session: Session, tmp_path: Path) -> None:
    result = ingest_un_wpp(
        session,
        fixture_path=UN_WPP_FIXTURE,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )
    review_un_wpp(session, result.source_release_id)
    ucdp_result = ingest_ucdp_annual(
        session,
        fixture_path=UCDP_ANNUAL_FIXTURE,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )
    review_ucdp_annual(session, ucdp_result.source_release_id)


def test_release_license_snapshot_is_complete_and_immutable(
    session: Session, tmp_path: Path
) -> None:
    result = ingest(session, tmp_path)
    assert result.source_release_id is not None
    license_row = session.get(SourceReleaseLicense, result.source_release_id)
    assert license_row is not None
    assert license_row.license_identifier == "US-PD-USGS"
    assert license_row.public_display_permission is True
    assert license_row.commercial_use_permission is True
    assert license_row.attribution_required is True
    session.commit()

    with pytest.raises(DBAPIError, match="append-only"):
        session.execute(
            update(SourceReleaseLicense)
            .where(SourceReleaseLicense.source_release_id == result.source_release_id)
            .values(public_display_permission=False)
        )
        session.commit()
    session.rollback()


def test_publication_cannot_accept_unreviewed_claims_as_a_side_effect(
    session: Session, tmp_path: Path
) -> None:
    ingest(session, tmp_path)
    with pytest.raises(ValueError, match="accepted imported claims"):
        publish_golden_profile(
            session,
            store=LocalFilesystemPublishedProfileStore(tmp_path / "published"),
        )
    assert session.scalar(select(func.count()).select_from(PublicationManifest)) == 0


def test_non_passed_required_check_blocks_publication(
    session: Session, tmp_path: Path
) -> None:
    result = ingest(session, tmp_path)
    assert result.source_release_id is not None
    accept_and_resolve_release(session, result.source_release_id)
    check = session.scalar(select(QualityCheck))
    assert check is not None
    check.status = "warning"
    session.flush()

    with pytest.raises(ValueError, match="all recorded quality checks"):
        publish_golden_profile(
            session,
            store=LocalFilesystemPublishedProfileStore(tmp_path / "published"),
        )
    assert session.scalar(select(func.count()).select_from(PublicationManifest)) == 0


def test_passing_unrelated_check_cannot_satisfy_adapter_quality_contract(
    session: Session, tmp_path: Path
) -> None:
    result = ingest(session, tmp_path)
    assert result.source_release_id is not None
    accept_and_resolve_release(session, result.source_release_id)
    check = session.scalar(select(QualityCheck))
    assert check is not None
    check.check_name = "unrelated_passing_check"
    session.flush()

    with pytest.raises(ValueError, match="missing required quality checks"):
        publish_golden_profile(
            session,
            store=LocalFilesystemPublishedProfileStore(tmp_path / "published"),
        )


def test_editorial_decisions_append_a_versioned_history(
    session: Session, tmp_path: Path
) -> None:
    result = ingest(session, tmp_path)
    assert result.source_release_id is not None
    accept_and_resolve_release(session, result.source_release_id)
    root = session.scalar(select(ResolvedClaim))
    assert root is not None

    deferred = record_editorial_selection(
        session,
        profile_date=date(1964, 3, 28),
        section_key="recorded_on_this_date",
        resolved_claim_id=root.id,
        status=EditorialSelectionStatus.DEFERRED,
        display_rank=None,
        rationale="Awaiting a second editorial pass.",
        reviewed_by="test-reviewer",
    )
    selected = record_editorial_selection(
        session,
        profile_date=date(1964, 3, 28),
        section_key="recorded_on_this_date",
        resolved_claim_id=root.id,
        status=EditorialSelectionStatus.SELECTED,
        display_rank=1,
        rationale="Approved after the second editorial pass.",
        reviewed_by="test-reviewer",
    )

    assert selected.id != deferred.id
    assert deferred.decision_version == 1
    assert selected.decision_version == 2
    assert (
        session.scalar(
            select(func.count())
            .select_from(EditorialSelection)
            .where(
                EditorialSelection.profile_date == date(1964, 3, 28),
                EditorialSelection.resolved_claim_id == root.id,
            )
        )
        == 2
    )
    session.add(
        EditorialSelection(
            profile_date=date(1964, 3, 28),
            section_key="recorded_on_this_date",
            resolved_claim_id=root.id,
            status=EditorialSelectionStatus.REJECTED.value,
            decision_version=2,
            display_rank=None,
            rationale="A concurrent duplicate version must fail.",
            reviewed_by="concurrent-test-reviewer",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_latest_editorial_decision_controls_publication(
    session: Session, tmp_path: Path
) -> None:
    result = ingest(session, tmp_path)
    assert result.source_release_id is not None
    accept_and_resolve_release(session, result.source_release_id)
    review_context(session, tmp_path)
    root = session.scalar(
        select(ResolvedClaim).where(
            ResolvedClaim.canonical_key
            == "usgs:official19640328033616_30:event_identity"
        )
    )
    assert root is not None
    record_editorial_selection(
        session,
        profile_date=date(1964, 3, 27),
        section_key="recorded_on_this_date",
        resolved_claim_id=root.id,
        status=EditorialSelectionStatus.REJECTED,
        display_rank=None,
        rationale="Withdrawn from this profile after review.",
        reviewed_by="test-reviewer",
    )
    for status in (
        EditorialSelectionStatus.SELECTED,
        EditorialSelectionStatus.DEFERRED,
        EditorialSelectionStatus.SELECTED,
    ):
        record_editorial_selection(
            session,
            profile_date=date(1964, 3, 27),
            section_key="evidence_notes",
            resolved_claim_id=root.id,
            status=status,
            display_rank=1 if status == EditorialSelectionStatus.SELECTED else None,
            rationale="A separate evidence-notes decision cannot authorize event use.",
            reviewed_by="test-reviewer",
        )

    with pytest.raises(ValueError, match="explicit editorial selection"):
        publish_golden_profile(
            session,
            store=LocalFilesystemPublishedProfileStore(tmp_path / "published"),
        )


def test_quality_statement_uses_a_derived_evidence_root(
    session: Session, tmp_path: Path
) -> None:
    result = ingest(session, tmp_path)
    assert result.source_release_id is not None
    accept_and_resolve_release(session, result.source_release_id)
    review_context(session, tmp_path)
    profile = publish_golden_profile(
        session,
        store=LocalFilesystemPublishedProfileStore(tmp_path / "published"),
    )
    evidence = session.scalar(
        select(PublicationStatementEvidence).where(
            PublicationStatementEvidence.publication_manifest_id
            == profile.publication_manifest_id,
            PublicationStatementEvidence.statement_path
            == "/sections/evidence_notes/0",
        )
    )
    quality = session.scalar(
        select(DerivedValue).where(
            DerivedValue.value_kind == "public_event_evidence_quality"
        )
    )
    assert evidence is not None and quality is not None
    assert evidence.derived_value_id == quality.id
    assert evidence.resolved_claim_id is None
    assert evidence.evidence_snapshot["root_type"] == "derived_value"


def test_persisted_lineage_determines_independence_roots(session: Session) -> None:
    source = Source(slug="lineage-source", name="Lineage source")
    session.add(source)
    session.flush()
    root = create_source_release(
        session,
        source_id=source.id,
        release_label="root",
        source_url="https://example.invalid/root",
        raw_storage_uri="test://root",
        raw_bytes=b"root",
        raw_record_count=1,
    )
    republished = create_source_release(
        session,
        source_id=source.id,
        release_label="republished",
        source_url="https://example.invalid/republished",
        raw_storage_uri="test://republished",
        raw_bytes=b"republished",
        raw_record_count=1,
    )
    derived = create_source_release(
        session,
        source_id=source.id,
        release_label="derived",
        source_url="https://example.invalid/derived",
        raw_storage_uri="test://derived",
        raw_bytes=b"derived",
        raw_record_count=1,
    )
    session.add_all(
        [
            SourceLineage(
                child_release_id=republished.id,
                parent_release_id=root.id,
                relationship=SourceLineageRelationship.REPUBLISHED,
            ),
            SourceLineage(
                child_release_id=derived.id,
                parent_release_id=republished.id,
                relationship=SourceLineageRelationship.DERIVED,
            ),
        ]
    )
    session.flush()

    assert lineage_root_ids(session, root.id) == frozenset({root.id})
    assert lineage_root_ids(session, republished.id) == frozenset({root.id})
    assert lineage_root_ids(session, derived.id) == frozenset({root.id})
