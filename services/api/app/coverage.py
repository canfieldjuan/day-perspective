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
        session, live_dates=live_dates, store=store
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
    candidates = [
        (entry.profile_date, entry.profile_type)
        for entry in session.scalars(select(CoverageEntry))
        if entry.profile_date not in live_dates
    ]
    session.commit()
    for profile_date, profile_type in candidates:
        with _date_transaction(session, profile_date, profile_type):
            entry = session.scalar(
                select(CoverageEntry).where(CoverageEntry.profile_date == profile_date)
            )
            if entry is None:
                continue
            manifest = _latest_published_manifest(session, profile_date)
            if manifest is not None and (
                store is None or _artifact_servable(store, manifest)
            ):
                live_dates.add(profile_date)
                continue
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


def coverage_entry(session: Session, profile_date: date) -> CoverageEntry | None:
    """The indexed row for one date, or None when the date is not indexed.

    Deliberately the raw entry: the reader-facing record, its nearest-richer
    neighbours, and the archive summary belong to the coverage API slice,
    which layers them on top of this.
    """
    return session.scalar(
        select(CoverageEntry).where(CoverageEntry.profile_date == profile_date)
    )
