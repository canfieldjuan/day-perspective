"""Resumable batch publication (epic #32, slice AA2).

The archive publishes tens of thousands of dates. A run must survive one bad
date, survive being killed, and be safe to run again.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.base import LocalFilesystemRawSourceStore
from app.batch_publication import (
    CONTEXT_BATCH_KIND,
    BatchPlanError,
    latest_batch_run,
    outstanding_dates,
    plan_context_dates,
    run_context_batch,
    start_batch_run,
)
from app.models import (
    BatchEntryStatus,
    BatchRunStatus,
    PublicationBatchEntry,
    PublicationManifest,
)
from app.services import LocalFilesystemPublishedProfileStore
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


def test_planning_requires_exactly_one_selection() -> None:
    with pytest.raises(BatchPlanError):
        plan_context_dates()
    with pytest.raises(BatchPlanError):
        plan_context_dates(year=1971, single_date=date(1971, 1, 1))
    with pytest.raises(BatchPlanError):
        plan_context_dates(from_date=date(1971, 1, 1))


def test_planning_a_year_covers_it_exactly_including_leap_days() -> None:
    assert len(plan_context_dates(year=1971)) == 365
    leap = plan_context_dates(year=1964)
    assert len(leap) == 366
    assert date(1964, 2, 29) in leap


def test_planning_rejects_unsupported_years_up_front() -> None:
    """An operator asking for 1900 has made one mistake, not 365 of them."""
    with pytest.raises(BatchPlanError, match="unsupported years: 1900"):
        plan_context_dates(year=1900)
    with pytest.raises(BatchPlanError):
        plan_context_dates(from_date=date(1949, 12, 30), to_date=date(1950, 1, 2))


@pytest.mark.integration
def test_a_batch_publishes_every_date_and_ledgers_each_outcome(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    dates = [date(1971, 3, 1), date(1971, 3, 2), date(1971, 3, 3)]
    run = start_batch_run(
        session,
        kind=CONTEXT_BATCH_KIND,
        requested={"dates": [value.isoformat() for value in dates]},
    )

    report = run_context_batch(session, store=store, dates=dates, batch_run=run)

    assert report.published == 3
    assert report.failed == 0
    assert run.status is BatchRunStatus.COMPLETED
    entries = list(
        session.scalars(
            select(PublicationBatchEntry).where(
                PublicationBatchEntry.batch_run_id == run.id
            )
        )
    )
    assert len(entries) == 3
    assert all(entry.status is BatchEntryStatus.PUBLISHED for entry in entries)
    assert all(entry.publication_manifest_id is not None for entry in entries)


@pytest.mark.integration
def test_one_unpublishable_date_does_not_end_the_run(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    """A 1949 date has no UN WPP coverage; the surrounding dates must still
    publish, and the failure must be ledgered with its reason."""
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    dates = [date(1971, 4, 1), date(1949, 6, 1), date(1971, 4, 2)]
    run = start_batch_run(
        session,
        kind=CONTEXT_BATCH_KIND,
        requested={"dates": [value.isoformat() for value in dates]},
    )

    report = run_context_batch(session, store=store, dates=dates, batch_run=run)

    assert report.published == 2
    assert report.failed == 1
    assert report.failures[0][0] == date(1949, 6, 1)
    assert run.status is BatchRunStatus.INTERRUPTED
    failed = session.scalar(
        select(PublicationBatchEntry).where(
            PublicationBatchEntry.batch_run_id == run.id,
            PublicationBatchEntry.status == BatchEntryStatus.FAILED,
        )
    )
    assert failed is not None and failed.detail
    assert session.scalar(
        select(PublicationManifest).where(
            PublicationManifest.profile_date == date(1971, 4, 2)
        )
    ), "A later date must publish despite an earlier failure."


@pytest.mark.integration
def test_resume_attempts_only_what_the_ledger_still_owes(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    dates = [date(1972, 5, 1), date(1949, 7, 1), date(1972, 5, 2)]
    run = start_batch_run(
        session,
        kind=CONTEXT_BATCH_KIND,
        requested={"dates": [value.isoformat() for value in dates]},
    )
    # Simulate a run killed after the first date.
    run_context_batch(session, store=store, dates=dates[:1], batch_run=run)

    owed = outstanding_dates(session, batch_run=run)
    assert owed == [date(1949, 7, 1), date(1972, 5, 2)]

    resumed = run_context_batch(session, store=store, dates=owed, batch_run=run)
    assert resumed.published == 1
    assert resumed.failed == 1

    only_failed = outstanding_dates(session, batch_run=run, only_failed=True)
    assert only_failed == [date(1949, 7, 1)]


@pytest.mark.integration
def test_rerunning_a_completed_batch_changes_nothing(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    dates = [date(1973, 6, 1), date(1973, 6, 2)]
    run = start_batch_run(
        session,
        kind=CONTEXT_BATCH_KIND,
        requested={"dates": [value.isoformat() for value in dates]},
    )
    run_context_batch(session, store=store, dates=dates, batch_run=run)

    second = run_context_batch(session, store=store, dates=dates, batch_run=run)

    assert second.unchanged == 2
    assert second.published == 0
    versions = list(
        session.scalars(
            select(PublicationManifest.version).where(
                PublicationManifest.profile_date.in_(dates)
            )
        )
    )
    assert versions == [1, 1]


@pytest.mark.integration
def test_a_dry_run_ledgers_intent_without_publishing(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    dates = [date(1974, 7, 1), date(1974, 7, 2)]
    run = start_batch_run(
        session,
        kind=CONTEXT_BATCH_KIND,
        requested={"dates": [value.isoformat() for value in dates]},
    )

    report = run_context_batch(
        session, store=store, dates=dates, batch_run=run, dry_run=True
    )

    assert report.skipped == 2
    assert report.published == 0
    assert not list(
        session.scalars(
            select(PublicationManifest).where(
                PublicationManifest.profile_date.in_(dates)
            )
        )
    )
    assert outstanding_dates(session, batch_run=run) == []


def test_a_stray_end_date_is_rejected_not_ignored() -> None:
    """Silently ignoring it would publish a different selection than asked."""
    with pytest.raises(BatchPlanError, match="only valid with --from-date"):
        plan_context_dates(single_date=date(1971, 1, 1), to_date=date(1971, 12, 31))
    with pytest.raises(BatchPlanError, match="only valid with --from-date"):
        plan_context_dates(year=1971, to_date=date(1972, 1, 31))


@pytest.mark.integration
def test_a_batch_never_buries_a_richer_published_profile(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    """Publishing annual context across a year must not hide an enriched
    date's recorded events behind a sparser, higher version."""
    from app.models import PublicationTier
    from app.services import (
        PublicationStatementEvidenceInput,
        create_claim,
        publish_day_profile,
        resolve_claim,
    )
    from tests.helpers import source_release

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    enriched_date = date(1976, 8, 4)
    release = source_release(session)
    claim = create_claim(
        session,
        source_release_id=release.id,
        source_record_locator="record:enriched-event",
        claim_type="synthetic_assertion",
        assertion_text="A recorded event for the enriched date.",
    )
    resolved = resolve_claim(
        session,
        canonical_key="test:enriched-event",
        resolved_value={"statement": "A recorded event."},
        rationale="Test-only recorded event.",
        supporting_claim_ids=[claim.id],
    )
    enriched = publish_day_profile(
        session,
        store=store,
        profile_date=enriched_date,
        profile_type=__import__("app.models", fromlist=["ProfileType"]).ProfileType.STANDARD_STATISTICAL,
        payload={
            "schema_version": "1",
            "date": enriched_date.isoformat(),
            "profile_type": "standard_statistical",
            "sections": {
                "recorded_on_this_date": [
                    {"statement_id": "event", "statement": "A recorded event."}
                ]
            },
        },
        statement_evidence=[
            PublicationStatementEvidenceInput(
                statement_path="/sections/recorded_on_this_date/0",
                resolved_claim_id=resolved.id,
            )
        ],
    )
    enriched_manifest_id = enriched.publication_manifest_id

    dates = [date(1976, 8, 3), enriched_date, date(1976, 8, 5)]
    run = start_batch_run(
        session,
        kind=CONTEXT_BATCH_KIND,
        requested={"dates": [value.isoformat() for value in dates]},
    )
    report = run_context_batch(session, store=store, dates=dates, batch_run=run)

    assert report.published == 2
    assert report.skipped == 1
    preserved = session.scalar(
        select(PublicationManifest)
        .where(PublicationManifest.profile_date == enriched_date)
        .order_by(PublicationManifest.version.desc())
        .limit(1)
    )
    assert preserved is not None
    assert preserved.id == enriched_manifest_id, (
        "The enriched date was superseded by a context-only version."
    )
    assert preserved.publication_tier is PublicationTier.REVIEWED_ENRICHED


@pytest.mark.integration
def test_the_standing_rule_never_reverses_a_reviewer_decision(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    """A human rejection must survive the context publisher, and publication
    must then fail closed rather than quietly re-selecting the root."""
    from app.governance import EditorialSelection, EditorialSelectionStatus
    from app.un_wpp import build_un_wpp_profile_content, publish_context_profile

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    profile_date = date(1977, 9, 9)
    content = build_un_wpp_profile_content(
        session, profile_date=profile_date, require_editorial_selection=False
    )
    rejected = content.evidence[0]
    section = rejected.statement_path.split("/")[2]
    from app.governance import record_editorial_selection

    record_editorial_selection(
        session,
        profile_date=profile_date,
        section_key=section,
        resolved_claim_id=rejected.resolved_claim_id,
        derived_value_id=rejected.derived_value_id,
        status=EditorialSelectionStatus.REJECTED,
        display_rank=None,
        rationale="A reviewer excluded this root for this date.",
        reviewed_by="human-reviewer",
    )
    session.commit()

    with pytest.raises(ValueError):
        publish_context_profile(session, store=store, profile_date=profile_date)
    session.rollback()

    latest = session.scalar(
        select(EditorialSelection)
        .where(
            EditorialSelection.profile_date == profile_date,
            EditorialSelection.section_key == section,
        )
        .order_by(EditorialSelection.decision_version.desc())
    )
    assert latest is not None
    assert latest.reviewed_by == "human-reviewer"
    assert latest.status == EditorialSelectionStatus.REJECTED.value


@pytest.mark.integration
def test_retry_failed_leaves_a_run_open_while_dates_remain_unattempted(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    dates = [date(1949, 8, 1), date(1978, 4, 1), date(1978, 4, 2)]
    run = start_batch_run(
        session,
        kind=CONTEXT_BATCH_KIND,
        requested={"dates": [value.isoformat() for value in dates]},
    )
    # Attempt only the first two: one fails, one succeeds, one never runs.
    run_context_batch(session, store=store, dates=dates[:2], batch_run=run)
    assert run.status is BatchRunStatus.INTERRUPTED

    # A retry of just the failures cannot close a run that still owes work.
    failed_only = outstanding_dates(session, batch_run=run, only_failed=True)
    assert failed_only == [date(1949, 8, 1)]
    run_context_batch(session, store=store, dates=failed_only, batch_run=run)
    assert run.status is BatchRunStatus.INTERRUPTED
    assert date(1978, 4, 2) in outstanding_dates(session, batch_run=run)


@pytest.mark.integration
def test_recovery_reaches_an_older_unfinished_run(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    """Year-by-year publication keeps going after a year is interrupted, so
    by recovery time the unfinished run is not the newest one."""
    from app.batch_publication import recoverable_batch_run

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    stranded_dates = [date(1990, 1, 1), date(1990, 1, 2)]
    stranded = start_batch_run(
        session,
        kind=CONTEXT_BATCH_KIND,
        requested={"dates": [value.isoformat() for value in stranded_dates]},
    )
    # Only the first date is attempted, as a killed year would leave it.
    run_context_batch(
        session, store=store, dates=stranded_dates[:1], batch_run=stranded
    )
    assert outstanding_dates(session, batch_run=stranded) == stranded_dates[1:]

    later_dates = [date(1991, 1, 1)]
    later = start_batch_run(
        session,
        kind=CONTEXT_BATCH_KIND,
        requested={"dates": [value.isoformat() for value in later_dates]},
    )
    run_context_batch(session, store=store, dates=later_dates, batch_run=later)
    assert outstanding_dates(session, batch_run=later) == []

    # The newest run owes nothing; recovery must still find the stranded one.
    newest = latest_batch_run(session, kind=CONTEXT_BATCH_KIND)
    assert newest is not None and newest.id == later.id
    recovered = recoverable_batch_run(session, kind=CONTEXT_BATCH_KIND)
    assert recovered is not None
    assert recovered.id == stranded.id

    report = run_context_batch(
        session,
        store=store,
        dates=outstanding_dates(session, batch_run=recovered),
        batch_run=recovered,
    )

    assert report.published == 1
    assert outstanding_dates(session, batch_run=stranded) == []
    # With nothing outstanding anywhere, recovery falls back to the newest.
    fallback = recoverable_batch_run(session, kind=CONTEXT_BATCH_KIND)
    assert fallback is not None and fallback.id == later.id
