"""Coverage index (epic #32, slice AA3).

Once every supported date carries annual context, a boolean "published"
flag stops being useful: the archive needs to say how rich each date is,
whether it holds a recorded event, and where the nearest date worth
travelling to lies. The index is derived from published manifests and their
immutable statement evidence — never from prose, and never from assumption.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    CoverageEntry,
    DayProfile,
    ProfileType,
    PublicationManifest,
    PublicationStatementEvidence,
    PublicationStatus,
    PublicationTier,
)
from app.services import PublishedProfileStore, _acquire_publication_lock

SECTION_KEYS = (
    "recorded_on_this_date",
    "typical_day_in_this_year",
    "wider_historical_context",
    "curated_claims",
    "derived_comparisons",
    "wonder_and_progress",
    "evidence_notes",
)
RECORDED_SECTION = "recorded_on_this_date"


@dataclass(frozen=True)
class CoverageRecord:
    """One date's richness, plus where the nearest richer dates lie."""

    profile_date: date
    profile_type: ProfileType
    publication_tier: PublicationTier
    has_recorded_event: bool
    sections: dict[str, int]
    nearest_enriched_before: date | None = None
    nearest_enriched_after: date | None = None
    nearest_recorded_event_before: date | None = None
    nearest_recorded_event_after: date | None = None


@dataclass
class CoverageSummary:
    """The shape of the archive: a constant-size answer about a large thing."""

    total_published: int = 0
    by_tier: dict[str, int] = field(default_factory=dict)
    with_recorded_event: int = 0
    earliest: date | None = None
    latest: date | None = None


@dataclass
class CoverageRebuildReport:
    """What a rebuild indexed, and what it refused to index."""

    indexed: int = 0
    dropped: int = 0
    unreadable: list[date] = field(default_factory=list)


def _section_counts(session: Session, manifest_id: UUID) -> dict[str, int]:
    """Count published statements per section from immutable evidence rows."""
    counts = dict.fromkeys(SECTION_KEYS, 0)
    for (path,) in session.execute(
        select(PublicationStatementEvidence.statement_path).where(
            PublicationStatementEvidence.publication_manifest_id == manifest_id
        )
    ):
        parts = path.split("/")
        if len(parts) >= 3 and parts[2] in counts:
            counts[parts[2]] += 1
    return counts


def upsert_coverage_entry(
    session: Session,
    *,
    manifest: PublicationManifest,
) -> CoverageEntry:
    """Record this date's richness. Called as publication's final step.

    Every field is derived from the manifest and its immutable statement
    evidence — nothing here reads the artifact or the editorial record, so
    there is no payload to thread through the callers and no second source
    that could disagree with this one.
    """
    counts = _section_counts(session, manifest.id)
    has_event = counts[RECORDED_SECTION] > 0
    entry = session.scalar(
        select(CoverageEntry).where(
            CoverageEntry.profile_date == manifest.profile_date
        )
    )
    values = {
        "profile_type": manifest.profile_type,
        "publication_manifest_id": manifest.id,
        "publication_tier": manifest.publication_tier,
        "has_recorded_event": has_event,
        "sections": counts,
        "refreshed_at": datetime.now(UTC),
    }
    if entry is None:
        entry = CoverageEntry(profile_date=manifest.profile_date, **values)
        session.add(entry)
    else:
        for key, value in values.items():
            setattr(entry, key, value)
    session.flush()
    return entry


def latest_published_manifests(session: Session) -> list[PublicationManifest]:
    """The manifest a reader would actually be served for each date."""
    newest = (
        select(
            PublicationManifest.profile_date.label("profile_date"),
            func.max(PublicationManifest.version).label("version"),
        )
        .join(
            DayProfile, DayProfile.publication_manifest_id == PublicationManifest.id
        )
        .where(PublicationManifest.status == PublicationStatus.PUBLISHED)
        .group_by(PublicationManifest.profile_date)
        .subquery()
    )
    return list(
        session.scalars(
            select(PublicationManifest)
            .join(
                newest,
                (PublicationManifest.profile_date == newest.c.profile_date)
                & (PublicationManifest.version == newest.c.version),
            )
            .where(PublicationManifest.status == PublicationStatus.PUBLISHED)
            .order_by(PublicationManifest.profile_date)
        )
    )


@contextmanager
def _date_transaction(
    session: Session, profile_date: date, profile_type: ProfileType
) -> Iterator[None]:
    """One date's work as its own locked transaction.

    The lock is transaction-scoped, so PostgreSQL releases it at commit, on
    the same backend that took it. A session-scoped lock plus an explicit
    unlock is unsafe here: committing inside the lock can return the
    connection to the pool, and the unlock may then run on a different
    backend, silently leaking the lock and blocking publication for that
    date. Committing per date also keeps row locks from accumulating across
    the run, and keeps advisory locks from accumulating with them.
    """
    _acquire_publication_lock(session, profile_date, profile_type)
    try:
        yield
        session.commit()
    except BaseException:
        session.rollback()
        raise


def rebuild_coverage_index(
    session: Session,
    *,
    store: PublishedProfileStore | None = None,
) -> CoverageRebuildReport:
    """Regenerate the whole index from published state.

    Deterministic and idempotent: dates whose newest publication disappeared
    or was superseded by an unpublished manifest are dropped rather than
    left describing an archive that no longer exists.

    Each date is its own locked transaction, so a correction that lands
    while this walks its snapshot wins instead of being overwritten by the
    manifest that was newest when the walk began, and nothing this run holds
    accumulates across the archive.
    """
    report = CoverageRebuildReport()
    snapshot = latest_published_manifests(session)
    session.commit()
    live_dates = set()
    for stale in snapshot:
        with _date_transaction(session, stale.profile_date, stale.profile_type):
            manifest = _latest_published_manifest(session, stale.profile_date)
            if manifest is None:
                # No published manifest with a profile row: not served.
                continue
            if store is not None and not _artifact_servable(store, manifest):
                # The day endpoint fails for this date. Indexing it anyway
                # would send readers to a page that cannot be served.
                report.unreadable.append(manifest.profile_date)
                continue
            upsert_coverage_entry(session, manifest=manifest)
            live_dates.add(manifest.profile_date)
    report.dropped = _drop_stale_entries(
        session,
        live_dates=live_dates,
        store=store,
        report_unreadable=report.unreadable,
    )
    report.indexed = len(live_dates)
    return report


def _artifact_servable(
    store: PublishedProfileStore, manifest: PublicationManifest
) -> bool:
    """Whether the day endpoint could serve this date.

    The read is a guard only: no indexed field comes from the payload, so
    there is nothing to carry forward and no second read to disagree with
    this one.
    """
    try:
        store.read(manifest.storage_uri, manifest.content_hash)
    except (OSError, RuntimeError, ValueError):
        return False
    return True


def _drop_stale_entries(
    session: Session,
    *,
    live_dates: set[date],
    store: PublishedProfileStore | None,
    report_unreadable: list[date],
) -> int:
    """Remove entries the archive no longer supports.

    Every entry that did not survive this pass is re-checked in its own
    locked transaction before deletion — including dates that were in the
    snapshot but were skipped, because a skipped date can be republished
    healthy while the rebuild is still running. Deleting such a row would
    leave the day endpoint serving a profile that coverage reports as
    missing.
    """
    dropped = 0
    # Candidates are every date this pass did not settle: entries that did
    # not survive, and dates skipped as unreadable. A date skipped with no
    # existing row would otherwise never get the locked recheck this
    # function promises, so a transient read failure would leave a healthy
    # date unindexed and fail the run.
    candidates = {
        entry.profile_date: entry.profile_type
        for entry in session.scalars(select(CoverageEntry))
        if entry.profile_date not in live_dates
    }
    for profile_date in report_unreadable:
        if profile_date in candidates:
            continue
        profile_type = session.scalar(
            select(PublicationManifest.profile_type).where(
                PublicationManifest.profile_date == profile_date
            )
        )
        if profile_type is not None:
            candidates[profile_date] = profile_type
    session.commit()
    for profile_date, profile_type in sorted(candidates.items()):
        with _date_transaction(session, profile_date, profile_type):
            manifest = _latest_published_manifest(session, profile_date)
            if manifest is not None and (
                store is None or _artifact_servable(store, manifest)
            ):
                # Servable now, whether or not it was during the first pass
                # and whether or not it already had a row.
                upsert_coverage_entry(session, manifest=manifest)
                live_dates.add(profile_date)
                if profile_date in report_unreadable:
                    report_unreadable.remove(profile_date)
                continue
            entry = session.scalar(
                select(CoverageEntry).where(CoverageEntry.profile_date == profile_date)
            )
            if entry is not None:
                session.delete(entry)
                dropped += 1
    return dropped


def _latest_published_manifest(
    session: Session, profile_date: date
) -> PublicationManifest | None:
    """The manifest a reader is served, mirroring the day endpoint exactly.

    The day endpoint joins the profile row before ordering by version, so an
    incomplete newest manifest does not make the date unservable — it falls
    back to the newest version that has a profile. Selecting the newest
    manifest and skipping when its profile is missing would drop coverage
    for a date the endpoint still serves.
    """
    return session.scalar(
        select(PublicationManifest)
        .join(DayProfile, DayProfile.publication_manifest_id == PublicationManifest.id)
        .where(
            PublicationManifest.profile_date == profile_date,
            PublicationManifest.status == PublicationStatus.PUBLISHED,
        )
        .order_by(PublicationManifest.version.desc())
        .limit(1)
    )


def reconcile_date_coverage(
    session: Session,
    *,
    profile_date: date,
    profile_type: ProfileType,
    store: PublishedProfileStore | None = None,
) -> None:
    """Re-derive one date's coverage from what is actually servable.

    The single answer to "this date changed, fix its index entry". It is
    the same step the rebuild performs per date, so reconciliation cannot
    disagree with a rebuild about what a date should look like.

    Deriving the whole answer, rather than adding or removing at each call
    site, is what makes it correct in the cases that individually caught me
    out: a missing artifact leaves nothing to quarantine but still must be
    unindexed; a failed *older* version must not unindex a date whose newer
    version is served; and the decision must happen under the date's
    publication lock so a concurrent correction is not undone.
    """
    with _date_transaction(session, profile_date, profile_type):
        manifest = _latest_published_manifest(session, profile_date)
        servable = manifest is not None and (
            store is None or _artifact_servable(store, manifest)
        )
        if manifest is not None and servable:
            upsert_coverage_entry(session, manifest=manifest)
            return
        entry = session.scalar(
            select(CoverageEntry).where(CoverageEntry.profile_date == profile_date)
        )
        if entry is not None:
            session.delete(entry)


def coverage_entry(session: Session, profile_date: date) -> CoverageEntry | None:
    """The indexed row for one date, or None when the date is not indexed.

    Deliberately the raw entry: the reader-facing record, its nearest-richer
    neighbours, and the archive summary belong to the coverage API slice,
    which layers them on top of this.
    """
    return session.scalar(
        select(CoverageEntry).where(CoverageEntry.profile_date == profile_date)
    )


def _neighbour(
    session: Session,
    *,
    profile_date: date,
    after: bool,
    require_recorded_event: bool,
) -> date | None:
    """The nearest date in one direction that offers more than context.

    This is the query the whole index exists for: without it, a reader on a
    context-only date can only step day by day through near-identical pages
    to find out whether anything else is there.
    """
    condition = (
        CoverageEntry.profile_date > profile_date
        if after
        else CoverageEntry.profile_date < profile_date
    )
    statement = select(CoverageEntry.profile_date).where(condition)
    if require_recorded_event:
        statement = statement.where(CoverageEntry.has_recorded_event.is_(True))
    else:
        statement = statement.where(
            CoverageEntry.publication_tier != PublicationTier.CONTEXT_ONLY
        )
    statement = statement.order_by(
        CoverageEntry.profile_date.asc()
        if after
        else CoverageEntry.profile_date.desc()
    ).limit(1)
    return session.scalar(statement)


def coverage_for_date(session: Session, profile_date: date) -> CoverageRecord | None:
    """One date's record, or None when the date is not indexed.

    None means no published profile, never an empty one: a date the archive
    does not hold must not be described as a date holding nothing.
    """
    entry = coverage_entry(session, profile_date)
    if entry is None:
        return None
    return CoverageRecord(
        profile_date=entry.profile_date,
        profile_type=entry.profile_type,
        publication_tier=entry.publication_tier,
        has_recorded_event=entry.has_recorded_event,
        sections=dict(entry.sections or {}),
        nearest_enriched_before=_neighbour(
            session,
            profile_date=profile_date,
            after=False,
            require_recorded_event=False,
        ),
        nearest_enriched_after=_neighbour(
            session,
            profile_date=profile_date,
            after=True,
            require_recorded_event=False,
        ),
        nearest_recorded_event_before=_neighbour(
            session,
            profile_date=profile_date,
            after=False,
            require_recorded_event=True,
        ),
        nearest_recorded_event_after=_neighbour(
            session,
            profile_date=profile_date,
            after=True,
            require_recorded_event=True,
        ),
    )


def coverage_summary(session: Session) -> CoverageSummary:
    """Aggregate in the database: this response is constant-size, and the
    archive it describes is not."""
    summary = CoverageSummary()
    summary.by_tier = dict.fromkeys((tier.value for tier in PublicationTier), 0)
    totals = session.execute(
        select(
            func.count(CoverageEntry.profile_date),
            func.count(CoverageEntry.profile_date).filter(
                CoverageEntry.has_recorded_event.is_(True)
            ),
            func.min(CoverageEntry.profile_date),
            func.max(CoverageEntry.profile_date),
        )
    ).one()
    summary.total_published = totals[0] or 0
    summary.with_recorded_event = totals[1] or 0
    summary.earliest = totals[2]
    summary.latest = totals[3]
    for tier, count in session.execute(
        select(
            CoverageEntry.publication_tier,
            func.count(CoverageEntry.profile_date),
        ).group_by(CoverageEntry.publication_tier)
    ):
        summary.by_tier[tier.value] = count
    return summary


def random_enriched_date(session: Session) -> date | None:
    """A uniformly random date offering more than annual context.

    Chosen in the database rather than by listing every enriched date,
    because the enriched set is expected to grow with source coverage.
    Returns None when the archive holds none, which the interface must
    treat as "hide the control" rather than "try anyway".
    """
    return session.scalar(
        select(CoverageEntry.profile_date)
        .where(CoverageEntry.publication_tier != PublicationTier.CONTEXT_ONLY)
        .order_by(func.random())
        .limit(1)
    )
