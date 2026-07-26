"""Coverage index (epic #32, slice AA3).

Once every 1950-2025 date carries annual context, "is anything published?"
stops being a useful question. The index answers the questions that replace
it: how rich is this date, does it hold a recorded event, which strata have
content, and where is the nearest date worth travelling to.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.base import LocalFilesystemRawSourceStore
from app.batch_publication import CONTEXT_BATCH_KIND, run_context_batch, start_batch_run
from app.coverage import coverage_entry, rebuild_coverage_index
from app.models import CoverageEntry, PublicationManifest, PublicationTier
from app.services import (
    LocalFilesystemPublishedProfileStore,
    PublicationStatementEvidenceInput,
    create_claim,
    publish_day_profile,
    resolve_claim,
)
from app.un_wpp import ingest_un_wpp, review_un_wpp

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "fixtures"
    / "un-wpp"
    / "wpp2024-world-1950-2025.csv"
)


@pytest.fixture()
def reviewed_un_wpp(session: Session, tmp_path: Path) -> None:
    result = ingest_un_wpp(
        session,
        fixture_path=FIXTURE,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )
    assert result.source_release_id is not None
    review_un_wpp(session, result.source_release_id)
    session.commit()


def delete_coverage_entries() -> Any:
    """Simulate an archive that predates the index (or a dropped table)."""
    from sqlalchemy import delete

    return delete(CoverageEntry)


def _synthetic_release(session: Session, label: str) -> Any:
    """A dedicated single-record source per enriched date.

    The shared helper uses a fixed slug (so it cannot be called twice), and
    reusing the UN WPP release would demand per-claim source-record hashes.
    """
    from app.models import LegalReviewStatus, Source
    from app.services import create_source_release

    source = Source(
        slug=f"test-source-{label}",
        name=f"Synthetic source for {label}",
        publisher="Test suite",
        canonical_url=f"https://example.invalid/{label}",
        legal_review_status=LegalReviewStatus.NOT_REQUIRED,
    )
    session.add(source)
    session.flush()
    return create_source_release(
        session,
        source_id=source.id,
        release_label=f"{label}-v1",
        source_url=f"https://example.invalid/{label}/v1",
        raw_storage_uri=f"test://raw/{label}-v1",
        raw_bytes=f"raw bytes for {label}".encode(),
        raw_record_count=1,
    )


def publish_enriched(
    session: Session,
    store: LocalFilesystemPublishedProfileStore,
    profile_date: date,
    *,
    label: str,
) -> None:
    from app.models import ProfileType

    release = _synthetic_release(session, label)
    claim = create_claim(
        session,
        source_release_id=release.id,
        source_record_locator=f"record:{label}",
        claim_type="synthetic_assertion",
        assertion_text=f"Recorded event for {label}.",
    )
    resolved = resolve_claim(
        session,
        canonical_key=f"test:{label}",
        resolved_value={"statement": "A recorded event."},
        rationale="Test-only recorded event.",
        supporting_claim_ids=[claim.id],
    )
    publish_day_profile(
        session,
        store=store,
        profile_date=profile_date,
        profile_type=ProfileType.STANDARD_STATISTICAL,
        payload={
            "schema_version": "1",
            "date": profile_date.isoformat(),
            "profile_type": "standard_statistical",
            "sections": {
                "recorded_on_this_date": [
                    {
                        "statement_id": "event",
                        "statement": "A recorded event.",
                        "details": {"quality_grade": "B"},
                    }
                ]
            },
            "quality": {"grade": "B", "explanation": "Single validated source."},
        },
        statement_evidence=[
            PublicationStatementEvidenceInput(
                statement_path="/sections/recorded_on_this_date/0",
                resolved_claim_id=resolved.id,
            )
        ],
    )


@pytest.mark.integration
def test_the_index_records_richness_not_merely_publication(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    context_date = date(1980, 5, 5)
    enriched_date = date(1980, 5, 7)
    run = start_batch_run(
        session,
        kind=CONTEXT_BATCH_KIND,
        requested={"dates": [context_date.isoformat()]},
    )
    run_context_batch(session, store=store, dates=[context_date], batch_run=run)
    publish_enriched(session, store, enriched_date, label="index-enriched")
    session.commit()

    rebuild_coverage_index(session)
    session.commit()

    context = coverage_entry(session, context_date)
    assert context is not None
    assert context.publication_tier is PublicationTier.CONTEXT_ONLY
    assert context.sections["recorded_on_this_date"] == 0
    assert context.sections["typical_day_in_this_year"] == 2
    assert context.sections["wider_historical_context"] == 3
    assert context.has_recorded_event is False

    enriched = coverage_entry(session, enriched_date)
    assert enriched is not None
    assert enriched.publication_tier is PublicationTier.REVIEWED_ENRICHED
    assert enriched.sections["recorded_on_this_date"] == 1
    assert enriched.has_recorded_event is True


@pytest.mark.integration
def test_unpublished_dates_are_absent_rather_than_reported_empty(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    """The index must never imply a profile exists for a date that has none."""
    rebuild_coverage_index(session)
    session.commit()
    assert coverage_entry(session, date(1955, 1, 1)) is None


@pytest.mark.integration
def test_rebuilding_is_idempotent_and_follows_supersession(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    """A regenerated index must describe the archive as it is now, including
    after a correction, and must not accumulate duplicate rows."""
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    profile_date = date(1983, 4, 4)
    run = start_batch_run(
        session,
        kind=CONTEXT_BATCH_KIND,
        requested={"dates": [profile_date.isoformat()]},
    )
    run_context_batch(session, store=store, dates=[profile_date], batch_run=run)
    session.commit()

    rebuild_coverage_index(session)
    session.commit()
    first = coverage_entry(session, profile_date)
    assert first is not None and first.publication_tier is PublicationTier.CONTEXT_ONLY

    rebuild_coverage_index(session)
    session.commit()
    assert (
        len(
            list(
                session.scalars(
                    select(CoverageEntry).where(
                        CoverageEntry.profile_date == profile_date
                    )
                )
            )
        )
        == 1
    )

    publish_enriched(session, store, profile_date, label="correction")
    session.commit()
    rebuild_coverage_index(session)
    session.commit()
    corrected = coverage_entry(session, profile_date)
    assert corrected is not None
    assert corrected.publication_tier is PublicationTier.REVIEWED_ENRICHED


@pytest.mark.integration
def test_publication_updates_coverage_without_a_full_rebuild(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    """Coverage is maintained as the last step of publication, so the index
    is never stale between bulk runs."""
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    profile_date = date(1984, 8, 8)
    run = start_batch_run(
        session,
        kind=CONTEXT_BATCH_KIND,
        requested={"dates": [profile_date.isoformat()]},
    )
    run_context_batch(session, store=store, dates=[profile_date], batch_run=run)
    session.commit()

    record = coverage_entry(session, profile_date)
    assert record is not None
    assert record.publication_tier is PublicationTier.CONTEXT_ONLY


# --- Round 1 review findings (PR #43) ------------------------------------


@pytest.mark.integration
def test_reconcile_repair_indexes_the_profile_it_completes(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repaired publication is served by /api/v1/day, so coverage must not
    keep reporting it missing until someone runs a full rebuild."""
    from app.models import ProfileType
    from app.services import reconcile_publications

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    profile_date = date(1975, 4, 30)
    claim = create_claim(
        session,
        source_release_id=_synthetic_release(session, "repair-coverage").id,
        source_record_locator="record:repair",
        claim_type="synthetic_assertion",
        assertion_text="A recorded event.",
    )
    resolved = resolve_claim(
        session,
        canonical_key="test:repair-coverage",
        resolved_value={"statement": "A recorded event."},
        rationale="Test-only recorded event.",
        supporting_claim_ids=[claim.id],
    )
    evidence = [
        PublicationStatementEvidenceInput(
            statement_path="/sections/recorded_on_this_date/0",
            resolved_claim_id=resolved.id,
        )
    ]

    from app import services as services_module

    def explode(*args: object, **inner: object) -> None:
        raise RuntimeError("Simulated crash before artifact promotion.")

    monkeypatch.setattr(services_module.StagedProfileWrite, "finalize", explode)
    with pytest.raises(RuntimeError, match="Simulated crash"):
        publish_day_profile(
            session,
            store=store,
            profile_date=profile_date,
            profile_type=ProfileType.STANDARD_STATISTICAL,
            payload={
                "schema_version": "1",
                "date": profile_date.isoformat(),
                "profile_type": "standard_statistical",
                "sections": {
                    "recorded_on_this_date": [
                        {"statement_id": "event", "statement": "A recorded event."}
                    ]
                },
            },
            statement_evidence=evidence,
        )
    monkeypatch.undo()
    session.rollback()

    report = reconcile_publications(session, store=store, repair=True)
    session.commit()

    assert report.completed_pending + report.abandoned_pending >= 1
    if report.completed_pending:
        record = coverage_entry(session, profile_date)
        assert record is not None, "repaired publication is absent from coverage"
        assert record.has_recorded_event is True


@pytest.mark.integration
def test_republishing_identical_content_still_heals_a_missing_entry(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    """Idempotent publication returns early; if that path skips coverage, an
    archive whose index was never built cannot be healed by re-running the
    publishers."""
    from app.un_wpp import publish_context_profile

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    profile_date = date(1983, 7, 4)
    publish_context_profile(session, store=store, profile_date=profile_date)
    session.commit()
    session.execute(delete_coverage_entries())
    session.commit()
    assert coverage_entry(session, profile_date) is None

    publish_context_profile(session, store=store, profile_date=profile_date)
    session.commit()

    assert coverage_entry(session, profile_date) is not None


@pytest.mark.integration
def test_rebuild_refuses_to_advertise_an_unreadable_artifact(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    """The day endpoint 503s on a missing artifact; coverage must not keep
    telling navigation that the date is worth visiting."""
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    profile_date = date(1984, 2, 2)
    run = start_batch_run(
        session,
        kind=CONTEXT_BATCH_KIND,
        requested={"dates": [profile_date.isoformat()]},
    )
    run_context_batch(session, store=store, dates=[profile_date], batch_run=run)
    session.commit()
    rebuild_coverage_index(session, store=store)
    session.commit()
    assert coverage_entry(session, profile_date) is not None

    for artifact in (tmp_path / "published").rglob("*.json"):
        artifact.unlink()

    result = rebuild_coverage_index(session, store=store)
    session.commit()

    assert coverage_entry(session, profile_date) is None
    assert result.unreadable == [profile_date]
    assert result.indexed == 0


@pytest.mark.integration
def test_rebuild_does_not_overwrite_a_newer_publication(
    session: Session, tmp_path: Path, reviewed_un_wpp: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A correction published while a rebuild is walking its snapshot must
    win: the rebuild re-reads each date under the publication lock."""
    from app import coverage as coverage_module
    from app.un_wpp import publish_context_profile

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    profile_date = date(1988, 8, 8)
    publish_context_profile(session, store=store, profile_date=profile_date)
    session.commit()
    stale = list(coverage_module.latest_published_manifests(session))
    stale_versions = {manifest.version for manifest in stale}
    assert stale_versions == {1}

    publish_context_profile(
        session, store=store, profile_date=profile_date, force_new_version=True
    )
    session.commit()
    current = session.scalar(
        select(PublicationManifest.version)
        .where(PublicationManifest.profile_date == profile_date)
        .order_by(PublicationManifest.version.desc())
        .limit(1)
    )
    assert current == 2

    monkeypatch.setattr(
        coverage_module, "latest_published_manifests", lambda _session: stale
    )
    rebuild_coverage_index(session, store=store)
    session.commit()

    entry = session.scalar(
        select(CoverageEntry).where(CoverageEntry.profile_date == profile_date)
    )
    assert entry is not None
    indexed_version = session.scalar(
        select(PublicationManifest.version).where(
            PublicationManifest.id == entry.publication_manifest_id
        )
    )
    assert indexed_version == 2, "rebuild indexed a superseded manifest"


# --- Round 2 review findings (PR #43) ------------------------------------


@pytest.mark.integration
def test_a_rebuild_keeps_a_date_published_while_it_was_running(
    session: Session, tmp_path: Path, reviewed_un_wpp: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A date published after the snapshot is not in it, so the cleanup pass
    would delete the row publication had just written."""
    from app import coverage as coverage_module
    from app.un_wpp import publish_context_profile

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    existing = date(1991, 5, 5)
    published_mid_rebuild = date(1991, 5, 6)
    publish_context_profile(session, store=store, profile_date=existing)
    session.commit()
    snapshot = list(coverage_module.latest_published_manifests(session))

    publish_context_profile(session, store=store, profile_date=published_mid_rebuild)
    session.commit()
    assert coverage_entry(session, published_mid_rebuild) is not None

    monkeypatch.setattr(
        coverage_module, "latest_published_manifests", lambda _session: snapshot
    )
    report = rebuild_coverage_index(session, store=store)
    session.commit()

    assert coverage_entry(session, published_mid_rebuild) is not None, (
        "the rebuild deleted a date published while it ran"
    )
    assert report.dropped == 0


@pytest.mark.integration
def test_a_rebuild_does_not_hold_one_lock_per_date(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    """At archive scale a transaction-scoped lock per date exhausts the lock
    pool and blocks corrections for the length of the run."""
    from sqlalchemy import text

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    dates = [date(1992, 7, day) for day in range(1, 7)]
    run = start_batch_run(
        session,
        kind=CONTEXT_BATCH_KIND,
        requested={"dates": [value.isoformat() for value in dates]},
    )
    run_context_batch(session, store=store, dates=dates, batch_run=run)
    session.commit()

    rebuild_coverage_index(session, store=store)

    held = session.execute(
        text(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
            "AND pid = pg_backend_pid()"
        )
    ).scalar_one()
    session.commit()

    assert held == 0, f"{held} advisory locks still held after the rebuild"


# --- Round 3 review findings (PR #43) ------------------------------------


@pytest.mark.integration
def test_a_rebuild_does_not_hold_row_locks_for_the_whole_run(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    """Releasing the advisory lock is not enough: an uncommitted upsert keeps
    the coverage row's write lock, blocking a correction to an
    already-processed date until the entire archive finishes."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import sessionmaker

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    dates = [date(1994, 3, day) for day in range(1, 5)]
    run = start_batch_run(
        session,
        kind=CONTEXT_BATCH_KIND,
        requested={"dates": [value.isoformat() for value in dates]},
    )
    run_context_batch(session, store=store, dates=dates, batch_run=run)
    session.commit()

    rebuild_coverage_index(session, store=store)

    # A separate connection must be able to touch an indexed row while the
    # rebuilding session is still open.
    # str() masks the password; the second connection needs the real URL.
    bind = session.get_bind()
    assert isinstance(bind, Engine)
    engine = create_engine(bind.url.render_as_string(hide_password=False))
    other = sessionmaker(bind=engine)()
    try:
        other.execute(text("SET lock_timeout = '2s'"))
        other.execute(
            text(
                "UPDATE coverage_entries SET refreshed_at = now() "
                "WHERE profile_date = :d"
            ),
            {"d": dates[0]},
        )
        other.commit()
    finally:
        other.close()
        engine.dispose()
    session.commit()

    assert coverage_entry(session, dates[0]) is not None


@pytest.mark.integration
def test_a_skipped_snapshot_date_is_rechecked_before_deletion(
    session: Session, tmp_path: Path, reviewed_un_wpp: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A date skipped for an unreadable artifact can be republished healthy
    while the rebuild runs; deleting its fresh row would leave the day
    endpoint serving a profile coverage reports as missing."""
    from app import coverage as coverage_module
    from app.un_wpp import publish_context_profile

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    profile_date = date(1995, 6, 6)
    publish_context_profile(session, store=store, profile_date=profile_date)
    session.commit()
    snapshot = list(coverage_module.latest_published_manifests(session))

    calls = {"n": 0}
    real_servable = coverage_module._artifact_servable

    def unservable_once(store_arg: Any, manifest: Any) -> bool:
        # Unservable while the snapshot pass looks at it, healthy by the
        # time the cleanup pass reconsiders it.
        calls["n"] += 1
        if calls["n"] == 1:
            return False
        result: bool = real_servable(store_arg, manifest)
        return result

    monkeypatch.setattr(coverage_module, "_artifact_servable", unservable_once)
    monkeypatch.setattr(
        coverage_module, "latest_published_manifests", lambda _session: snapshot
    )
    report = rebuild_coverage_index(session, store=store)
    session.commit()

    assert coverage_entry(session, profile_date) is not None, (
        "the rebuild deleted a date that was healthy by the time it was dropped"
    )
    assert report.dropped == 0


@pytest.mark.integration
def test_coverage_follows_the_version_the_day_endpoint_serves(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    """The day endpoint joins the profile row before ordering versions, so a
    newest manifest without a profile does not make the date unservable.
    Coverage must index the version a reader is actually served.

    reconcile's missing_profiles counter exists precisely because this state
    occurs; publication is atomic enough that it cannot be reached through
    the publish path, so the row is written directly.
    """
    from sqlalchemy import inspect as sql_inspect

    from app.un_wpp import publish_context_profile

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    profile_date = date(1997, 8, 8)
    served = publish_context_profile(session, store=store, profile_date=profile_date)
    session.commit()
    served_manifest_id = served.publication_manifest_id

    served_manifest = session.get(PublicationManifest, served_manifest_id)
    assert served_manifest is not None
    columns = sql_inspect(PublicationManifest).mapper.column_attrs
    clone = PublicationManifest(
        **{
            attribute.key: getattr(served_manifest, attribute.key)
            for attribute in columns
            if attribute.key != "id"
        }
    )
    clone.version = served_manifest.version + 1
    clone.content_hash = "f" * 64
    clone.storage_uri = served_manifest.storage_uri + ".v2"
    session.add(clone)
    session.commit()

    rebuild_coverage_index(session, store=store)
    session.commit()

    record = coverage_entry(session, profile_date)
    assert record is not None, (
        "coverage dropped a date the day endpoint still serves"
    )
    indexed = session.scalar(
        select(CoverageEntry.publication_manifest_id).where(
            CoverageEntry.profile_date == profile_date
        )
    )
    assert indexed == served_manifest_id, (
        "coverage indexed a manifest with no profile row"
    )


# --- Round 5 review findings (PR #43) ------------------------------------




# --- Round 8 review findings (PR #43) ------------------------------------


@pytest.mark.integration
def test_quarantining_a_bad_artifact_removes_it_from_the_index(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    """Repair makes a hash-mismatched date unservable; leaving it indexed
    points navigation at a page that returns 503."""
    from app.services import reconcile_publications
    from app.un_wpp import publish_context_profile

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    profile_date = date(2001, 5, 5)
    publish_context_profile(session, store=store, profile_date=profile_date)
    session.commit()
    assert coverage_entry(session, profile_date) is not None

    for artifact in (tmp_path / "published").rglob("*.json"):
        artifact.write_text('{"tampered": true}', encoding="utf-8")

    report = reconcile_publications(session, store=store, repair=True)
    session.commit()

    assert report.hash_mismatches >= 1
    assert coverage_entry(session, profile_date) is None, (
        "a quarantined date is still advertised by coverage"
    )


@pytest.mark.integration
def test_a_date_that_recovers_mid_rebuild_is_not_reported_unreadable(
    session: Session, tmp_path: Path, reviewed_un_wpp: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI exits non-zero on an unreadable artifact. A date that is
    healthy by the end of the run must not fail it."""
    from app import coverage as coverage_module
    from app.un_wpp import publish_context_profile

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    profile_date = date(2002, 6, 6)
    publish_context_profile(session, store=store, profile_date=profile_date)
    session.commit()

    calls = {"n": 0}
    real_servable = coverage_module._artifact_servable

    def unservable_once(store_arg: Any, manifest: Any) -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            return False
        result: bool = real_servable(store_arg, manifest)
        return result

    monkeypatch.setattr(coverage_module, "_artifact_servable", unservable_once)
    report = rebuild_coverage_index(session, store=store)
    session.commit()

    assert coverage_entry(session, profile_date) is not None
    assert report.unreadable == [], (
        "a recovered date still fails the rebuild"
    )


# --- Round 9 review findings (PR #43) ------------------------------------


@pytest.mark.integration
def test_a_missing_artifact_is_unindexed_even_with_nothing_to_quarantine(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    """Verification fails whether the artifact is corrupt or absent; only
    the corrupt case leaves a file to move."""
    from app.services import reconcile_publications
    from app.un_wpp import publish_context_profile

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    profile_date = date(2003, 7, 7)
    publish_context_profile(session, store=store, profile_date=profile_date)
    session.commit()
    assert coverage_entry(session, profile_date) is not None

    for artifact in (tmp_path / "published").rglob("*.json"):
        artifact.unlink()

    reconcile_publications(session, store=store, repair=True)
    session.commit()

    assert coverage_entry(session, profile_date) is None, (
        "a date with no artifact is still advertised by coverage"
    )


@pytest.mark.integration
def test_quarantining_an_older_version_keeps_the_served_one_indexed(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    """Unindexing by date would remove a date whose newer version is
    perfectly servable."""
    from app.models import PublicationStatus
    from app.services import reconcile_publications
    from app.un_wpp import publish_context_profile

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    profile_date = date(2004, 8, 8)
    first = publish_context_profile(session, store=store, profile_date=profile_date)
    session.commit()
    first_manifest = session.get(PublicationManifest, first.publication_manifest_id)
    assert first_manifest is not None
    older_uri = first_manifest.storage_uri

    publish_context_profile(
        session, store=store, profile_date=profile_date, force_new_version=True
    )
    session.commit()
    served = session.scalar(
        select(PublicationManifest)
        .where(
            PublicationManifest.profile_date == profile_date,
            PublicationManifest.status == PublicationStatus.PUBLISHED,
        )
        .order_by(PublicationManifest.version.desc())
        .limit(1)
    )
    assert served is not None and served.version == 2

    # Corrupt only the older version's artifact.
    (tmp_path / "published" / older_uri).write_text("{}", encoding="utf-8")

    report = reconcile_publications(session, store=store, repair=True)
    session.commit()

    assert report.hash_mismatches >= 1
    record = coverage_entry(session, profile_date)
    assert record is not None, (
        "quarantining an older version unindexed a servable date"
    )
    assert record.publication_manifest_id == served.id


# --- Round 10 review findings (PR #43) -----------------------------------


@pytest.mark.integration
def test_a_transiently_unreadable_new_date_is_still_indexed(
    session: Session, tmp_path: Path, reviewed_un_wpp: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A date with no existing entry that fails its first read must still
    get the locked recheck, or a transient failure leaves a healthy date
    unindexed and fails the run."""
    from app import coverage as coverage_module
    from app.un_wpp import publish_context_profile

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    profile_date = date(2005, 9, 9)
    publish_context_profile(session, store=store, profile_date=profile_date)
    session.commit()

    # Drop the row, as an earlier rebuild would have while the artifact was
    # unavailable: the date is published and servable but not indexed.
    session.execute(delete_coverage_entries())
    session.commit()
    assert coverage_entry(session, profile_date) is None

    calls = {"n": 0}
    real_servable = coverage_module._artifact_servable

    def unservable_once(store_arg: Any, manifest: Any) -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            return False
        result: bool = real_servable(store_arg, manifest)
        return result

    monkeypatch.setattr(coverage_module, "_artifact_servable", unservable_once)
    report = rebuild_coverage_index(session, store=store)
    session.commit()

    assert coverage_entry(session, profile_date) is not None, (
        "a transient read failure left a servable date unindexed"
    )
    assert report.unreadable == []
    assert report.indexed == 1
