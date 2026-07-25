"""Issue #4 acceptance criteria plus the archive-activation fail-closed
publication contract (epic #32, slice AA0): concurrent publishers allocate
distinct versions or fail cleanly, concurrent claim decisions yield one
durable terminal decision, staged-artifact cleanup is transaction-owned,
interrupted publications leave a durable pending state that reconciliation
completes, and reconciliation reports and repairs every orphan class
without silently destroying artifacts."""

from __future__ import annotations

import os
import threading
import time
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
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


@pytest.mark.integration
def test_publication_completes_under_production_session_configuration(
    session: Session, migrated_database: str, tmp_path: Path
) -> None:
    """Production SessionLocal disables autoflush (app/database.py), so a
    pending manifest-status UPDATE is not implicitly emitted before the
    dependent day_profiles INSERT; SQLAlchemy's unit of work orders a child
    insert ahead of a parent update, and the validate_day_profile_manifest
    trigger then rejects the row. Completion must flush the published status
    explicitly rather than relying on autoflush."""
    store = LocalFilesystemPublishedProfileStore(tmp_path)
    evidence = statement_evidence(session)
    session.commit()

    engine = create_engine(migrated_database)
    production_like = sessionmaker(
        bind=engine, autocommit=False, autoflush=False, expire_on_commit=False
    )
    try:
        with production_like() as production_session:
            profile = publish_day_profile(
                production_session,
                store=store,
                profile_date=PROFILE_DATE,
                profile_type=PROFILE_TYPE,
                payload=payload("Published without autoflush."),
                statement_evidence=evidence,
            )
            manifest = production_session.get(
                PublicationManifest, profile.publication_manifest_id
            )
            assert manifest is not None
            assert manifest.status == PublicationStatus.PUBLISHED
            assert store.read(manifest.storage_uri, manifest.content_hash)

        with production_like() as verifier:
            reconciled = reconcile_publications(verifier, store=store, repair=True)
            verifier.commit()
            assert reconciled.healthy_published == 1
            assert reconciled.abandoned_pending == 0
    finally:
        engine.dispose()


@pytest.mark.integration
def test_identical_republish_is_a_no_op_even_when_supersession_is_offered(
    session: Session, tmp_path: Path
) -> None:
    """Real publishers (usgs.publish_golden_profile) always pass the previous
    manifest as a supersession candidate, so idempotency must be decided by
    content, not by whether the caller offered to supersede. Only
    force_new_version may create a second version of identical content."""
    store = LocalFilesystemPublishedProfileStore(tmp_path)
    evidence = statement_evidence(session)

    first = publish(session, store, evidence, "Rerun-safe content.")
    repeat = publish_day_profile(
        session,
        store=store,
        profile_date=PROFILE_DATE,
        profile_type=PROFILE_TYPE,
        payload=payload("Rerun-safe content."),
        statement_evidence=evidence,
        supersedes_manifest_id=first.publication_manifest_id,
        supersedes_day_profile_id=first.id,
    )
    assert repeat.id == first.id
    assert list(
        session.scalars(
            select(PublicationManifest.version).where(
                PublicationManifest.profile_date == PROFILE_DATE
            )
        )
    ) == [1]
    assert len(list((tmp_path / "day" / PROFILE_DATE.isoformat()).glob("*.json"))) == 1

    forced = publish_day_profile(
        session,
        store=store,
        profile_date=PROFILE_DATE,
        profile_type=PROFILE_TYPE,
        payload=payload("Rerun-safe content."),
        statement_evidence=evidence,
        supersedes_manifest_id=first.publication_manifest_id,
        supersedes_day_profile_id=first.id,
        force_new_version=True,
    )
    assert forced.id != first.id
    assert sorted(
        session.scalars(
            select(PublicationManifest.version).where(
                PublicationManifest.profile_date == PROFILE_DATE
            )
        )
    ) == [1, 2]


def _crash_pending_publication(
    session: Session,
    store: LocalFilesystemPublishedProfileStore,
    evidence: list[PublicationStatementEvidenceInput],
    statement: str,
    monkeypatch: pytest.MonkeyPatch,
    **kwargs: object,
) -> None:
    """Leave a durable DRAFT manifest by crashing before artifact promotion."""
    from app import services as services_module

    def explode(*args: object, **inner: object) -> None:
        raise RuntimeError("Simulated crash before artifact promotion.")

    monkeypatch.setattr(services_module.StagedProfileWrite, "finalize", explode)
    with pytest.raises(RuntimeError, match="Simulated crash"):
        publish_day_profile(
            session,
            store=store,
            profile_date=PROFILE_DATE,
            profile_type=PROFILE_TYPE,
            payload=payload(statement),
            statement_evidence=evidence,
            **kwargs,  # type: ignore[arg-type]
        )
    monkeypatch.undo()
    session.rollback()


@pytest.mark.integration
def test_reconcile_completes_an_interrupted_correction(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A correction's manifest carries supersedes_manifest_id, and the
    lifecycle trigger rejects a day profile that omits the matching profile
    predecessor, so reconciliation must derive it from the manifest."""
    store = LocalFilesystemPublishedProfileStore(tmp_path)
    evidence = statement_evidence(session)
    first = publish(session, store, evidence, "Original before correction.")

    _crash_pending_publication(
        session,
        store,
        evidence,
        "Corrected content.",
        monkeypatch,
        supersedes_manifest_id=first.publication_manifest_id,
        supersedes_day_profile_id=first.id,
    )

    report = reconcile_publications(session, store=store, repair=True)
    session.commit()

    assert report.completed_pending == 1
    assert report.abandoned_pending == 0
    corrected = session.scalar(
        select(PublicationManifest)
        .where(PublicationManifest.profile_date == PROFILE_DATE)
        .order_by(PublicationManifest.version.desc())
        .limit(1)
    )
    assert corrected is not None and corrected.version == 2
    assert corrected.status == PublicationStatus.PUBLISHED
    corrected_profile = session.scalar(
        select(DayProfile).where(DayProfile.publication_manifest_id == corrected.id)
    )
    assert corrected_profile is not None
    assert corrected_profile.supersedes_day_profile_id == first.id


@pytest.mark.integration
def test_reconcile_survives_a_corrupt_pending_artifact(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Truncated JSON raises ValueError from the store, which must be
    reported and quarantined rather than aborting the whole run."""
    store = LocalFilesystemPublishedProfileStore(tmp_path)
    evidence = statement_evidence(session)
    _crash_pending_publication(
        session, store, evidence, "Corrupt pending publication.", monkeypatch
    )

    pending = session.scalar(
        select(PublicationManifest).where(
            PublicationManifest.status == PublicationStatus.DRAFT
        )
    )
    assert pending is not None
    destination = tmp_path / pending.storage_uri
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("{not valid json", encoding="utf-8")
    (destination.with_name(destination.name + ".tmp")).unlink(missing_ok=True)

    report = reconcile_publications(session, store=store, repair=True)
    session.commit()

    assert report.hash_mismatches == 1
    assert report.abandoned_pending == 1
    assert (tmp_path / "quarantine").exists()


@pytest.mark.integration
def test_report_only_reconcile_recognizes_recoverable_temps(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report-only mode must assess recoverability honestly instead of
    calling a recoverable publication abandoned."""
    store = LocalFilesystemPublishedProfileStore(tmp_path)
    evidence = statement_evidence(session)
    _crash_pending_publication(
        session, store, evidence, "Recoverable pending publication.", monkeypatch
    )

    report = reconcile_publications(session, store=store, repair=False)

    assert report.completed_pending == 1
    assert report.abandoned_pending == 0
    still_pending = session.scalar(
        select(PublicationManifest).where(
            PublicationManifest.status == PublicationStatus.DRAFT
        )
    )
    assert still_pending is not None, "Report-only mode must not mutate state."


@pytest.mark.integration
def test_reconcile_repair_serializes_against_publication(
    session: Session, migrated_database: str, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repair must take the per-profile publication lock before inspecting or
    changing pending state, or it can withdraw a manifest a publisher is
    about to complete."""
    store = LocalFilesystemPublishedProfileStore(tmp_path)
    evidence = statement_evidence(session)
    _crash_pending_publication(
        session, store, evidence, "Pending during repair.", monkeypatch
    )
    session.commit()
    # Drive the abandonment path specifically: it decides a manifest is
    # unrecoverable and must not do so while a publisher holds the lock and is
    # about to promote its artifact.
    pending = session.scalar(
        select(PublicationManifest).where(
            PublicationManifest.status == PublicationStatus.DRAFT
        )
    )
    assert pending is not None
    (tmp_path / pending.storage_uri).with_name(
        Path(pending.storage_uri).name + ".tmp"
    ).unlink(missing_ok=True)

    lock_key = publication_advisory_lock_key(PROFILE_DATE, PROFILE_TYPE)
    blocker_engine = create_engine(migrated_database)
    worker_engine = create_engine(migrated_database)
    finished = threading.Event()
    failure: list[BaseException] = []
    try:
        blocker = blocker_engine.connect()
        blocker.begin()
        blocker.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": lock_key},
        )

        def repair() -> None:
            factory = sessionmaker(bind=worker_engine, expire_on_commit=False)
            try:
                with factory() as worker:
                    reconcile_publications(worker, store=store, repair=True)
                    worker.commit()
                finished.set()
            except BaseException as error:  # pragma: no cover - surfaced below
                failure.append(error)
                finished.set()

        thread = threading.Thread(target=repair, daemon=True)
        thread.start()
        time.sleep(0.6)
        assert not finished.is_set(), (
            "Reconciliation repaired pending state while the publication lock "
            "for that date was held."
        )
        blocker.rollback()
        blocker.close()
        assert finished.wait(timeout=10)
        thread.join(timeout=5)
        if failure:
            raise failure[0]
    finally:
        blocker_engine.dispose()
        worker_engine.dispose()


@pytest.mark.integration
def test_quarantine_retains_every_bad_artifact(
    session: Session, tmp_path: Path
) -> None:
    """Quarantine must never overwrite an artifact it previously retained."""
    store = LocalFilesystemPublishedProfileStore(tmp_path)
    evidence = statement_evidence(session)
    published = publish(session, store, evidence, "Healthy publication.")
    manifest = session.get(PublicationManifest, published.publication_manifest_id)
    assert manifest is not None
    artifact = tmp_path / manifest.storage_uri

    artifact.write_text('{"tampered": 1}', encoding="utf-8")
    reconcile_publications(session, store=store, repair=True)
    artifact.write_text('{"tampered": 2}', encoding="utf-8")
    reconcile_publications(session, store=store, repair=True)

    quarantined = sorted(
        path for path in (tmp_path / "quarantine").rglob("*") if path.is_file()
    )
    assert len(quarantined) == 2, (
        "The second quarantine overwrote the first retained artifact."
    )
    assert {path.read_text(encoding="utf-8") for path in quarantined} == {
        '{"tampered": 1}',
        '{"tampered": 2}',
    }


@pytest.mark.integration
def test_failed_retry_preserves_a_pre_existing_staged_payload(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retry reuses the deterministic temp path; if the retry's own commit
    fails it must not delete the payload the earlier interrupted transaction
    left behind."""
    store = LocalFilesystemPublishedProfileStore(tmp_path)
    evidence = statement_evidence(session)
    _crash_pending_publication(
        session, store, evidence, "Pending payload to preserve.", monkeypatch
    )
    pending = session.scalar(
        select(PublicationManifest).where(
            PublicationManifest.status == PublicationStatus.DRAFT
        )
    )
    assert pending is not None
    temp = (tmp_path / pending.storage_uri).with_name(
        Path(pending.storage_uri).name + ".tmp"
    )
    assert temp.exists()

    from app.models import ResolvedClaim

    session.add(
        ResolvedClaim(
            canonical_key="test:force-retry-commit-failure",
            version=1,
            resolved_value={"invalid": True},
            resolution_method="editorial_review",
            comparability_status="unknown",
            rationale="Missing evidence forces the retry commit to fail.",
        )
    )
    with pytest.raises(DBAPIError):
        publish(session, store, evidence, "Pending payload to preserve.")
    session.rollback()

    assert temp.exists(), (
        "The failed retry deleted a staged payload it did not create, "
        "turning a recoverable publication into an abandoned one."
    )


@pytest.mark.integration
def test_promotion_is_idempotent_when_a_peer_swept_the_shared_temp(
    session: Session, tmp_path: Path
) -> None:
    """Two publishers of identical content share the deterministic temp path;
    the loser must still complete rather than raising FileNotFoundError."""
    store = LocalFilesystemPublishedProfileStore(tmp_path)
    body = payload("Concurrent identical content.")
    first = store.stage_versioned(PROFILE_DATE, 1, body)
    second = store.stage_versioned(PROFILE_DATE, 1, body)

    first.finalize()
    assert not (tmp_path / "day" / PROFILE_DATE.isoformat()).joinpath(
        "profile-v1.json.tmp"
    ).exists()

    second.finalize()
    assert store.read(second.storage_uri, second.expected_hash) == body


@pytest.mark.integration
def test_report_only_reconcile_flags_a_missing_day_profile(
    session: Session, tmp_path: Path
) -> None:
    """The default report must not describe the exact state repair exists to
    fix as healthy."""
    from app.services import content_hash

    store = LocalFilesystemPublishedProfileStore(tmp_path)
    evidence = statement_evidence(session)
    publish(session, store, evidence, "Healthy neighbour publication.")

    # Day profiles attached to published manifests are immutable, so the
    # orphan state is built the only way it can legitimately arise: a
    # published manifest whose profile row was never created.
    orphan_date = date(1970, 1, 2)
    orphan_payload: dict[str, object] = {
        "schema_version": "1",
        "date": orphan_date.isoformat(),
        "profile_type": PROFILE_TYPE.value,
        "sections": {"evidence_notes": []},
    }
    staged = store.stage_versioned(orphan_date, 1, orphan_payload)
    staged.finalize()
    session.add(
        PublicationManifest(
            profile_date=orphan_date,
            profile_type=PROFILE_TYPE,
            version=1,
            editorial_revision=1,
            status=PublicationStatus.PUBLISHED,
            published_at=datetime.now(UTC),
            content_hash=content_hash(orphan_payload),
            source_snapshot_hash="0" * 64,
            storage_uri=staged.storage_uri,
            code_version="test",
            metadata_json={},
        )
    )
    session.commit()

    report = reconcile_publications(session, store=store, repair=False)

    assert report.missing_profiles == 1
    assert report.healthy_published == 1
    assert not list(
        session.scalars(
            select(DayProfile).where(DayProfile.profile_date == orphan_date)
        )
    ), "Report-only mode must not restore rows."

    repaired = reconcile_publications(session, store=store, repair=True)
    session.commit()
    assert repaired.missing_profiles == 1
    assert list(
        session.scalars(
            select(DayProfile).where(DayProfile.profile_date == orphan_date)
        )
    )


@pytest.mark.integration
def test_staging_makes_the_directory_entry_durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A committed pending manifest promises a durable staged payload, which
    requires fsyncing the parent directory, not just the file."""
    store = LocalFilesystemPublishedProfileStore(tmp_path)
    synced: list[int] = []
    real_fsync = os.fsync

    def recording_fsync(fd: int) -> None:
        synced.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    staged = store.stage_versioned(PROFILE_DATE, 1, payload("Durable staging."))
    monkeypatch.undo()

    assert len(synced) >= 2, (
        "Staging fsynced the file but not the directory entry that names it."
    )
    assert staged.temporary_path is not None and staged.temporary_path.exists()
