"""Issue #4 acceptance criteria plus the archive-activation fail-closed
publication contract (epic #32, slice AA0): concurrent publishers allocate
distinct versions or fail cleanly, concurrent claim decisions yield one
durable terminal decision, staged-artifact cleanup is transaction-owned,
interrupted publications leave a durable pending state that reconciliation
completes, and reconciliation reports and repairs every orphan class
without silently destroying artifacts."""

from __future__ import annotations

import threading
import time
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.governance import (
    ClaimReviewDecision,
    ReviewDecisionValue,
    record_claim_review,
)
from app.models import (
    Claim,
    ClaimAssertionStatus,
    DayProfile,
    ProfileType,
    PublicationManifest,
    PublicationStatus,
)
from app.services import (
    LocalFilesystemPublishedProfileStore,
    PublicationStatementEvidenceInput,
    create_claim,
    publication_advisory_lock_key,
    publish_day_profile,
    reconcile_publications,
    resolve_claim,
)
from tests.helpers import source_release

PROFILE_DATE = date(1969, 7, 20)
PROFILE_TYPE = ProfileType.STANDARD_STATISTICAL


def payload(statement: str) -> dict[str, object]:
    return {
        "schema_version": "1",
        "date": PROFILE_DATE.isoformat(),
        "profile_type": PROFILE_TYPE.value,
        "sections": {
            "evidence_notes": [
                {"statement_id": "atomicity-test", "statement": statement}
            ]
        },
    }


def statement_evidence(session: Session) -> list[PublicationStatementEvidenceInput]:
    release = source_release(session)
    claim = create_claim(
        session,
        source_release_id=release.id,
        source_record_locator="record:atomicity",
        claim_type="synthetic_assertion",
        assertion_text="Synthetic provenance for atomicity testing.",
    )
    resolved = resolve_claim(
        session,
        canonical_key="test:atomicity",
        resolved_value={"statement": "Synthetic atomicity provenance."},
        rationale="Test-only atomicity provenance.",
        supporting_claim_ids=[claim.id],
    )
    return [
        PublicationStatementEvidenceInput(
            statement_path="/sections/evidence_notes/0",
            resolved_claim_id=resolved.id,
        )
    ]


def publish(
    session: Session,
    store: LocalFilesystemPublishedProfileStore,
    evidence: list[PublicationStatementEvidenceInput],
    statement: str,
) -> DayProfile:
    return publish_day_profile(
        session,
        store=store,
        profile_date=PROFILE_DATE,
        profile_type=PROFILE_TYPE,
        payload=payload(statement),
        statement_evidence=evidence,
    )


@pytest.mark.integration
def test_concurrent_publication_serializes_on_the_advisory_lock(
    session: Session, migrated_database: str, tmp_path: Path
) -> None:
    store = LocalFilesystemPublishedProfileStore(tmp_path)
    evidence = statement_evidence(session)
    session.commit()

    lock_key = publication_advisory_lock_key(PROFILE_DATE, PROFILE_TYPE)
    blocker_engine = create_engine(migrated_database)
    other_engine = create_engine(migrated_database)
    completed = threading.Event()
    failure: list[BaseException] = []

    try:
        blocker = blocker_engine.connect()
        blocker.begin()
        blocker.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": lock_key},
        )

        def competing_publish() -> None:
            factory = sessionmaker(bind=other_engine, expire_on_commit=False)
            try:
                with factory() as other_session:
                    publish(other_session, store, evidence, "Competing publication.")
                completed.set()
            except BaseException as error:  # pragma: no cover - surfaced below
                failure.append(error)
                completed.set()

        thread = threading.Thread(target=competing_publish, daemon=True)
        thread.start()

        time.sleep(0.6)
        assert not completed.is_set(), (
            "The competing publisher proceeded while the publication advisory "
            "lock for this date was held."
        )

        blocker.rollback()
        blocker.close()
        assert completed.wait(timeout=10), "Competing publisher never finished."
        thread.join(timeout=5)
        if failure:
            raise failure[0]
    finally:
        blocker_engine.dispose()
        other_engine.dispose()

    session.expire_all()
    manifests = list(
        session.scalars(
            select(PublicationManifest).where(
                PublicationManifest.profile_date == PROFILE_DATE
            )
        )
    )
    assert [m.status for m in manifests] == [PublicationStatus.PUBLISHED]
    assert manifests[0].version == 1
    assert store.read(manifests[0].storage_uri, manifests[0].content_hash)


@pytest.mark.integration
def test_identical_republish_is_idempotent(session: Session, tmp_path: Path) -> None:
    store = LocalFilesystemPublishedProfileStore(tmp_path)
    evidence = statement_evidence(session)

    first = publish(session, store, evidence, "Identical content.")
    second = publish(session, store, evidence, "Identical content.")

    assert second.id == first.id
    versions = list(
        session.scalars(
            select(PublicationManifest.version).where(
                PublicationManifest.profile_date == PROFILE_DATE
            )
        )
    )
    assert versions == [1]


@pytest.mark.integration
def test_changed_content_supersedes_with_a_new_version(
    session: Session, tmp_path: Path
) -> None:
    store = LocalFilesystemPublishedProfileStore(tmp_path)
    evidence = statement_evidence(session)

    first = publish(session, store, evidence, "Original content.")
    second = publish_day_profile(
        session,
        store=store,
        profile_date=PROFILE_DATE,
        profile_type=PROFILE_TYPE,
        payload=payload("Corrected content."),
        statement_evidence=evidence,
        supersedes_manifest_id=first.publication_manifest_id,
        supersedes_day_profile_id=first.id,
    )

    assert second.id != first.id
    manifests = {
        manifest.version: manifest
        for manifest in session.scalars(
            select(PublicationManifest).where(
                PublicationManifest.profile_date == PROFILE_DATE
            )
        )
    }
    assert set(manifests) == {1, 2}
    assert store.read(manifests[2].storage_uri, manifests[2].content_hash)


@pytest.mark.integration
def test_interrupted_promotion_is_durable_and_reconcile_completes(
    session: Session,
    migrated_database: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalFilesystemPublishedProfileStore(tmp_path)
    evidence = statement_evidence(session)
    session.commit()

    from app import services as services_module

    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Simulated crash before artifact promotion.")

    monkeypatch.setattr(
        services_module.StagedProfileWrite, "finalize", explode
    )
    with pytest.raises(RuntimeError, match="Simulated crash"):
        publish(session, store, evidence, "Interrupted publication.")
    monkeypatch.undo()
    session.rollback()

    engine = create_engine(migrated_database)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with factory() as fresh:
            pending = list(
                fresh.scalars(
                    select(PublicationManifest).where(
                        PublicationManifest.profile_date == PROFILE_DATE
                    )
                )
            )
            assert [m.status for m in pending] == [PublicationStatus.DRAFT], (
                "The interrupted publication must leave a durable pending "
                "manifest rather than nothing or a published lie."
            )
            assert not list(
                fresh.scalars(
                    select(DayProfile).where(DayProfile.profile_date == PROFILE_DATE)
                )
            )

            report = reconcile_publications(fresh, store=store, repair=True)
            fresh.commit()
            assert report.completed_pending == 1

            manifests = list(
                fresh.scalars(
                    select(PublicationManifest).where(
                        PublicationManifest.profile_date == PROFILE_DATE
                    )
                )
            )
            assert [m.status for m in manifests] == [PublicationStatus.PUBLISHED]
            assert store.read(manifests[0].storage_uri, manifests[0].content_hash)
            profiles = list(
                fresh.scalars(
                    select(DayProfile).where(DayProfile.profile_date == PROFILE_DATE)
                )
            )
            assert len(profiles) == 1
    finally:
        engine.dispose()


@pytest.mark.integration
def test_reconcile_reports_orphans_mismatches_and_stale_temps(
    session: Session, tmp_path: Path
) -> None:
    store = LocalFilesystemPublishedProfileStore(tmp_path)
    evidence = statement_evidence(session)
    published = publish(session, store, evidence, "Healthy publication.")
    manifest = session.get(PublicationManifest, published.publication_manifest_id)
    assert manifest is not None

    orphan = tmp_path / "day" / "1970-01-01" / "profile-v1.json"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text("{\"orphaned\": true}", encoding="utf-8")

    artifact = tmp_path / manifest.storage_uri
    artifact.write_text("{\"tampered\": true}", encoding="utf-8")

    stale = tmp_path / "day" / PROFILE_DATE.isoformat() / ".profile-stale"
    stale.write_text("partial", encoding="utf-8")
    old = time.time() - 7200
    import os

    os.utime(stale, (old, old))

    report = reconcile_publications(
        session, store=store, repair=True, stale_temp_max_age_seconds=3600
    )

    assert report.orphan_artifacts == 1
    assert report.hash_mismatches == 1
    assert report.stale_temps_removed == 1
    assert not stale.exists()
    assert not orphan.exists() or "quarantine" in str(orphan)
    quarantine_root = tmp_path / "quarantine"
    assert quarantine_root.exists(), (
        "Reconciliation must quarantine bad artifacts, never silently delete "
        "or silently ignore them."
    )


@pytest.mark.integration
def test_concurrent_terminal_claim_decisions_yield_one_decision(
    session: Session, migrated_database: str
) -> None:
    release = source_release(session)
    claim = create_claim(
        session,
        source_release_id=release.id,
        source_record_locator="record:decision-race",
        claim_type="synthetic_assertion",
        assertion_text="Claim contested by two reviewers.",
    )
    claim.assertion_status = ClaimAssertionStatus.CANDIDATE
    session.commit()

    engine = create_engine(migrated_database)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with factory() as stale_session:
            stale_claim = stale_session.get(Claim, claim.id)
            assert stale_claim is not None

            record_claim_review(
                session,
                claim=claim,
                decision=ReviewDecisionValue.ACCEPTED,
                rationale="First reviewer accepts.",
                reviewed_by="reviewer-a",
            )
            session.commit()

            with pytest.raises(ValueError):
                record_claim_review(
                    stale_session,
                    claim=stale_claim,
                    decision=ReviewDecisionValue.REJECTED,
                    rationale="Second reviewer rejects concurrently.",
                    reviewed_by="reviewer-b",
                )
            stale_session.rollback()
    finally:
        engine.dispose()

    session.expire_all()
    decisions = list(
        session.scalars(
            select(ClaimReviewDecision).where(ClaimReviewDecision.claim_id == claim.id)
        )
    )
    terminal = [d for d in decisions if d.decision in ("accepted", "rejected")]
    assert len(terminal) == 1
    refreshed = session.get(Claim, claim.id)
    assert refreshed is not None
    assert refreshed.assertion_status == ClaimAssertionStatus.ACCEPTED


@pytest.mark.integration
def test_unrelated_nested_rollback_preserves_completed_publication(
    session: Session, tmp_path: Path
) -> None:
    store = LocalFilesystemPublishedProfileStore(tmp_path)
    evidence = statement_evidence(session)
    published = publish(session, store, evidence, "Publication before rollback.")
    manifest = session.get(PublicationManifest, published.publication_manifest_id)
    assert manifest is not None
    artifact = tmp_path / manifest.storage_uri
    assert artifact.exists()

    from app.models import Methodology

    nested = session.begin_nested()
    session.add(
        Methodology(
            slug="doomed-nested-methodology",
            version="1",
            name="Doomed nested work",
            description="Rolled back to prove artifact ownership.",
            code_version="test",
            definition_hash="f" * 64,
        )
    )
    nested.rollback()
    session.rollback()

    assert artifact.exists(), (
        "An unrelated rollback must never remove another transaction's "
        "finalized artifact."
    )
    with sessionmaker(bind=session.get_bind(), expire_on_commit=False)() as check:
        manifests = list(
            check.scalars(
                select(PublicationManifest).where(
                    PublicationManifest.profile_date == PROFILE_DATE
                )
            )
        )
        assert [m.status for m in manifests] == [PublicationStatus.PUBLISHED]
