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
