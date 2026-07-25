from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Claim,
    ClaimAssertionStatus,
    ComparabilityStatus,
    LegalReviewStatus,
    ResolutionMethod,
    ResolvedClaim,
    ResolvedClaimEvidence,
    Source,
    SourceRelease,
)
from app.services import create_claim, create_source_release, resolve_claim, supersede_claim
from tests.helpers import source_release


@pytest.mark.integration
def test_source_release_creation_persists_raw_checksum(session: Session) -> None:
    release = source_release(session)
    session.commit()
    assert release.raw_checksum_sha256 == hashlib.sha256(b"test raw source bytes").hexdigest()


def test_source_release_rejects_a_checksum_that_conflicts_with_raw_bytes(session: Session) -> None:
    source = Source(
        slug="checksum-conflict-source",
        name="Checksum conflict test source",
        publisher="Test suite",
        canonical_url="https://example.invalid/checksum-conflict",
        legal_review_status=LegalReviewStatus.NOT_REQUIRED,
    )
    session.add(source)
    session.flush()
    with pytest.raises(ValueError, match="does not match"):
        create_source_release(
            session,
            source_id=source.id,
            release_label="test-v1",
            source_url="https://example.invalid/checksum-conflict/v1",
            raw_storage_uri="test://raw/checksum-conflict",
            raw_record_count=1,
            raw_bytes=b"actual bytes",
            raw_checksum_sha256=hashlib.sha256(b"different bytes").hexdigest(),
        )


@pytest.mark.integration
def test_source_release_is_immutable_after_ingestion(session: Session) -> None:
    release = source_release(session)
    session.commit()
    with pytest.raises(DBAPIError):
        session.execute(
            update(SourceRelease).where(SourceRelease.id == release.id).values(release_label="forbidden")
        )
        session.commit()
    session.rollback()


@pytest.mark.integration
def test_claim_creation_requires_source_release(session: Session) -> None:
    release = source_release(session)
    claim = create_claim(
        session,
        source_release_id=release.id,
        source_record_locator="record:1",
        claim_type="synthetic_assertion",
        assertion_text="Synthetic test assertion.",
    )
    session.commit()
    assert claim.source_release_id == release.id

    session.add(
        Claim(
            source_release_id=uuid.uuid4(),
            source_record_locator="no-release",
            claim_type="invalid",
            assertion_text="This must fail the foreign key.",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_multi_record_claim_requires_its_source_record_hash(
    session: Session,
) -> None:
    source = Source(
        slug="multi-record-source",
        name="Multi-record test source",
        publisher="Test suite",
        canonical_url="https://example.invalid/multi-record",
        legal_review_status=LegalReviewStatus.NOT_REQUIRED,
    )
    session.add(source)
    session.flush()
    release = create_source_release(
        session,
        source_id=source.id,
        release_label="multi-v1",
        source_url="https://example.invalid/multi-record/v1",
        raw_storage_uri="test://raw/multi-record",
        raw_record_count=2,
        raw_bytes=b"two raw records",
    )

    with pytest.raises(ValueError, match="multi-record"):
        create_claim(
            session,
            source_release_id=release.id,
            source_record_locator="record:missing-hash",
            claim_type="synthetic_assertion",
            assertion_text="A hash is required.",
        )


@pytest.mark.integration
def test_claim_supersession_creates_a_new_versioned_assertion(session: Session) -> None:
    release = source_release(session)
    prior = create_claim(
        session,
        source_release_id=release.id,
        source_record_locator="record:2",
        claim_type="synthetic_assertion",
        assertion_text="Earlier test assertion.",
    )
    replacement = supersede_claim(
        session,
        prior_claim=prior,
        assertion_text="Corrected test assertion.",
    )
    session.commit()
    assert prior.assertion_status == ClaimAssertionStatus.SUPERSEDED
    assert replacement.supersedes_claim_id == prior.id
    assert replacement.assertion_status == ClaimAssertionStatus.CANDIDATE


@pytest.mark.integration
def test_resolved_claim_retains_supporting_and_dissenting_claim_references(session: Session) -> None:
    release = source_release(session)
    supporting = create_claim(
        session,
        source_release_id=release.id,
        source_record_locator="record:3",
        claim_type="synthetic_assertion",
        assertion_text="Supporting test assertion.",
    )
    dissenting = create_claim(
        session,
        source_release_id=release.id,
        source_record_locator="record:4",
        claim_type="synthetic_assertion",
        assertion_text="Dissenting test assertion.",
    )
    resolved = resolve_claim(
        session,
        canonical_key="test:resolved",
        resolved_value={"statement": "Resolved solely for invariant coverage."},
        rationale="The service must preserve both evidence stances.",
        supporting_claim_ids=[supporting.id],
        dissenting_claim_ids=[dissenting.id],
    )
    session.commit()
    evidence = session.scalars(
        select(ResolvedClaimEvidence).where(ResolvedClaimEvidence.resolved_claim_id == resolved.id)
    ).all()
    assert {(item.claim_id, item.stance) for item in evidence} == {
        (supporting.id, "supporting"),
        (dissenting.id, "dissenting"),
    }


def test_resolved_claim_must_supersede_latest_same_key_version(
    session: Session,
) -> None:
    release = source_release(session)
    claim = create_claim(
        session,
        source_release_id=release.id,
        source_record_locator="record:linear-history",
        claim_type="synthetic_assertion",
        assertion_text="Linear history evidence.",
    )
    first = resolve_claim(
        session,
        canonical_key="test:linear-history",
        resolved_value={"version": 1},
        rationale="First version.",
        supporting_claim_ids=[claim.id],
    )
    second = resolve_claim(
        session,
        canonical_key="test:linear-history",
        resolved_value={"version": 2},
        rationale="Second version.",
        supporting_claim_ids=[claim.id],
        supersedes_resolved_claim_id=first.id,
    )

    with pytest.raises(ValueError, match="latest version"):
        resolve_claim(
            session,
            canonical_key="test:linear-history",
            resolved_value={"version": 3},
            rationale="Invalid branch.",
            supporting_claim_ids=[claim.id],
            supersedes_resolved_claim_id=first.id,
        )

    assert second.version == 2


@pytest.mark.integration
def test_resolved_claim_cannot_commit_without_evidence(session: Session) -> None:
    session.add(
        ResolvedClaim(
            canonical_key="test:unbacked-resolution",
            version=1,
            resolved_value={"statement": "This must not be publishable evidence."},
            resolution_method=ResolutionMethod.EDITORIAL_REVIEW,
            comparability_status=ComparabilityStatus.UNKNOWN,
            rationale="Direct database invariant test.",
        )
    )
    with pytest.raises(DBAPIError, match="resolved claims require"):
        session.commit()
    session.rollback()


@pytest.mark.integration
def test_resolved_claim_evidence_cannot_be_moved_from_its_only_parent(session: Session) -> None:
    release = source_release(session)
    first_claim = create_claim(
        session,
        source_release_id=release.id,
        source_record_locator="record:move-first",
        claim_type="synthetic_assertion",
        assertion_text="First move-guard assertion.",
    )
    second_claim = create_claim(
        session,
        source_release_id=release.id,
        source_record_locator="record:move-second",
        claim_type="synthetic_assertion",
        assertion_text="Second move-guard assertion.",
    )
    first_resolved = resolve_claim(
        session,
        canonical_key="test:move-first",
        resolved_value={"statement": "First resolved claim."},
        rationale="Direct update-path guard coverage.",
        supporting_claim_ids=[first_claim.id],
    )
    second_resolved = resolve_claim(
        session,
        canonical_key="test:move-second",
        resolved_value={"statement": "Second resolved claim."},
        rationale="Direct update-path guard coverage.",
        supporting_claim_ids=[second_claim.id],
    )
    session.commit()
    with pytest.raises(DBAPIError, match="resolved claims require"):
        session.execute(
            update(ResolvedClaimEvidence)
            .where(ResolvedClaimEvidence.resolved_claim_id == first_resolved.id)
            .values(resolved_claim_id=second_resolved.id)
        )
        session.commit()
    session.rollback()


@pytest.mark.integration
def test_resolved_claim_supporting_evidence_cannot_be_deleted_from_its_only_parent(
    session: Session,
) -> None:
    release = source_release(session)
    claim = create_claim(
        session,
        source_release_id=release.id,
        source_record_locator="record:delete-supporting",
        claim_type="synthetic_assertion",
        assertion_text="Delete-guard assertion.",
    )
    resolved = resolve_claim(
        session,
        canonical_key="test:delete-supporting",
        resolved_value={"statement": "Delete-guard resolution."},
        rationale="Direct delete-path guard coverage.",
        supporting_claim_ids=[claim.id],
    )
    session.commit()
    evidence = session.scalar(
        select(ResolvedClaimEvidence).where(ResolvedClaimEvidence.resolved_claim_id == resolved.id)
    )
    assert evidence is not None
    session.delete(evidence)
    with pytest.raises(DBAPIError, match="resolved claims require"):
        session.commit()
    session.rollback()
