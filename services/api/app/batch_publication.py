"""Resumable, fail-closed batch publication (epic #32, slice AA2).

Publishing an archive of tens of thousands of dates must survive a single
bad date, a killed process, and a rerun. Every date is its own transaction,
every outcome is ledgered, and a run that dies mid-flight is resumed from
its ledger rather than restarted from the beginning.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    BatchEntryStatus,
    BatchRunStatus,
    DayProfile,
    PublicationBatchEntry,
    PublicationBatchRun,
    PublicationManifest,
)
from app.services import LocalFilesystemPublishedProfileStore
from app.un_wpp import (
    SUPPORTED_YEARS,
    publish_context_profile,
    richer_published_profile,
)

CONTEXT_BATCH_KIND = "context-profiles"


class BatchPlanError(ValueError):
    """The requested date selection is not publishable."""


@dataclass
class BatchReport:
    batch_run_id: UUID | None = None
    requested: int = 0
    published: int = 0
    unchanged: int = 0
    failed: int = 0
    skipped: int = 0
    failures: list[tuple[date, str]] = field(default_factory=list)

    @property
    def completed(self) -> int:
        return self.published + self.unchanged


def date_range(start: date, end: date) -> Iterator[date]:
    if end < start:
        raise BatchPlanError("The batch end date precedes its start date.")
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def plan_context_dates(
    *,
    single_date: date | None = None,
    year: int | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[date]:
    """Resolve one flag combination into the dates a batch will attempt.

    Dates outside UN WPP's supported years are rejected up front rather than
    failing one by one: an operator asking for 1900 has made a mistake worth
    naming, not 365 identical failures worth ledgering.
    """
    provided = [single_date is not None, year is not None, from_date is not None]
    if sum(provided) != 1:
        raise BatchPlanError(
            "Choose exactly one of --date, --year, or --from-date/--to-date."
        )
    if to_date is not None and from_date is None:
        # Silently ignoring it would publish a different selection than the
        # operator asked for.
        raise BatchPlanError("--to-date is only valid with --from-date.")
    if single_date is not None:
        dates = [single_date]
    elif year is not None:
        dates = list(date_range(date(year, 1, 1), date(year, 12, 31)))
    else:
        assert from_date is not None
        if to_date is None:
            raise BatchPlanError("--from-date requires --to-date.")
        dates = list(date_range(from_date, to_date))
    unsupported = sorted({value.year for value in dates} - SUPPORTED_YEARS)
    if unsupported:
        raise BatchPlanError(
            "Context profiles require UN WPP coverage; unsupported years: "
            + ", ".join(str(year) for year in unsupported)
        )
    return dates


def _record_entry(
    session: Session,
    *,
    batch_run_id: UUID,
    profile_date: date,
    status: BatchEntryStatus,
    detail: str | None = None,
    manifest_id: UUID | None = None,
) -> None:
    existing = session.scalar(
        select(PublicationBatchEntry).where(
            PublicationBatchEntry.batch_run_id == batch_run_id,
            PublicationBatchEntry.profile_date == profile_date,
        )
    )
    if existing is None:
        session.add(
            PublicationBatchEntry(
                batch_run_id=batch_run_id,
                profile_date=profile_date,
                status=status,
                detail=detail,
                publication_manifest_id=manifest_id,
            )
        )
    else:
        existing.status = status
        existing.detail = detail
        existing.publication_manifest_id = manifest_id
    session.commit()


def start_batch_run(
    session: Session, *, kind: str, requested: dict[str, object]
) -> PublicationBatchRun:
    run = PublicationBatchRun(kind=kind, requested=requested)
    session.add(run)
    session.commit()
    return run


def latest_batch_run(
    session: Session, *, kind: str = CONTEXT_BATCH_KIND
) -> PublicationBatchRun | None:
    return session.scalar(
        select(PublicationBatchRun)
        .where(PublicationBatchRun.kind == kind)
        .order_by(PublicationBatchRun.started_at.desc())
        .limit(1)
    )


def recoverable_batch_run(
    session: Session,
    *,
    kind: str = CONTEXT_BATCH_KIND,
    only_failed: bool = False,
) -> PublicationBatchRun | None:
    """The oldest run that still owes dates, else the most recent run.

    Recovery must not be limited to the newest run. A year-by-year archive
    publication keeps going after a year fails or is killed, so by the time
    an operator recovers, several newer runs exist and the unfinished one is
    not the latest. Draining oldest-first means repeated --resume finishes
    every outstanding run rather than looking only at the last.
    """
    candidates = session.scalars(
        select(PublicationBatchRun)
        .where(PublicationBatchRun.kind == kind)
        .order_by(PublicationBatchRun.started_at.asc())
    )
    newest: PublicationBatchRun | None = None
    for run in candidates:
        newest = run
        if outstanding_dates(session, batch_run=run, only_failed=only_failed):
            return run
    return newest


def outstanding_dates(
    session: Session, *, batch_run: PublicationBatchRun, only_failed: bool = False
) -> list[date]:
    """Dates a resumed run still owes: never attempted, or previously failed."""
    requested = batch_run.requested or {}
    planned = [
        date.fromisoformat(str(value)) for value in requested.get("dates", [])
    ]
    entries = {
        entry.profile_date: entry
        for entry in session.scalars(
            select(PublicationBatchEntry).where(
                PublicationBatchEntry.batch_run_id == batch_run.id
            )
        )
    }
    if only_failed:
        return [
            profile_date
            for profile_date in planned
            if entries.get(profile_date) is not None
            and entries[profile_date].status == BatchEntryStatus.FAILED
        ]
    return [
        profile_date
        for profile_date in planned
        if profile_date not in entries
        or entries[profile_date].status == BatchEntryStatus.FAILED
    ]


def run_context_batch(
    session: Session,
    *,
    store: LocalFilesystemPublishedProfileStore,
    dates: Sequence[date],
    batch_run: PublicationBatchRun,
    dry_run: bool = False,
    force_new_version: bool = False,
) -> BatchReport:
    """Publish each date in its own transaction, ledgering every outcome.

    A failing date is recorded and the run continues: one unpublishable date
    must never cost the rest of the archive.
    """
    report = BatchReport(batch_run_id=batch_run.id, requested=len(dates))
    for profile_date in dates:
        if dry_run:
            report.skipped += 1
            _record_entry(
                session,
                batch_run_id=batch_run.id,
                profile_date=profile_date,
                status=BatchEntryStatus.SKIPPED,
                detail="dry run",
            )
            continue
        preserved = richer_published_profile(session, profile_date=profile_date)
        if preserved is not None:
            report.skipped += 1
            _record_entry(
                session,
                batch_run_id=batch_run.id,
                profile_date=profile_date,
                status=BatchEntryStatus.SKIPPED,
                detail="preserved a richer published profile",
                manifest_id=preserved.publication_manifest_id,
            )
            continue
        previous = session.scalar(
            select(PublicationManifest.id)
            .where(PublicationManifest.profile_date == profile_date)
            .order_by(PublicationManifest.version.desc())
            .limit(1)
        )
        try:
            profile: DayProfile = publish_context_profile(
                session,
                store=store,
                profile_date=profile_date,
                force_new_version=force_new_version,
            )
        except Exception as error:  # one date must not end the run
            session.rollback()
            report.failed += 1
            report.failures.append((profile_date, str(error)))
            _record_entry(
                session,
                batch_run_id=batch_run.id,
                profile_date=profile_date,
                status=BatchEntryStatus.FAILED,
                detail=str(error)[:1000],
            )
            continue
        unchanged = previous is not None and profile.publication_manifest_id == previous
        if unchanged:
            report.unchanged += 1
        else:
            report.published += 1
        _record_entry(
            session,
            batch_run_id=batch_run.id,
            profile_date=profile_date,
            status=(
                BatchEntryStatus.UNCHANGED if unchanged else BatchEntryStatus.PUBLISHED
            ),
            manifest_id=profile.publication_manifest_id,
        )
    # Completion is a property of the whole ledgered plan: a --retry-failed
    # invocation that fixes its subset must not close a run that still owes
    # never-attempted dates.
    session.flush()
    still_owed = outstanding_dates(session, batch_run=batch_run)
    if still_owed:
        batch_run.status = BatchRunStatus.INTERRUPTED
        batch_run.completed_at = None
    else:
        batch_run.status = BatchRunStatus.COMPLETED
        batch_run.completed_at = datetime.now(UTC)
    session.commit()
    return report
