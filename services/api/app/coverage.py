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
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.governance import EditorialSelection, EditorialSelectionStatus
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
#: What review actually happened for a date, derived from editorial-selection
#: rows rather than from the presence of evidence. A recorded event is not a
#: reviewed one: per-date human review exists as data or it does not exist.
CoverageReviewStatus = Literal["reviewed", "rule_selected", "unreviewed"]
REVIEWED_STATUS: CoverageReviewStatus = "reviewed"
STANDING_RULE_REVIEW_STATUS: CoverageReviewStatus = "rule_selected"
UNREVIEWED_STATUS: CoverageReviewStatus = "unreviewed"
REVIEW_STATUSES: tuple[CoverageReviewStatus, ...] = (
    REVIEWED_STATUS,
    STANDING_RULE_REVIEW_STATUS,
    UNREVIEWED_STATUS,
)


def as_review_status(value: str) -> CoverageReviewStatus:
    """Narrow a stored status, weakening rather than inventing a claim.

    A row written by a future version with a status this one cannot name
    must not be read as something stronger than it is.
    """
    if value in REVIEW_STATUSES:
        return cast(CoverageReviewStatus, value)
    return UNREVIEWED_STATUS


@dataclass
class CoverageRebuildReport:
    """What a rebuild indexed, and what it refused to index."""

    indexed: int = 0
    dropped: int = 0
    index_version: int = 1
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


#: Known grades from strongest to weakest. A grade outside this vocabulary
#: cannot be ordered against it, so it sorts as the weakest thing present:
#: "we cannot establish how good this is" must never read as "good".
GRADE_ORDER = ("A", "B", "C", "D", "E", "F")


def _weakness(grade: str) -> tuple[int, str]:
    try:
        return (GRADE_ORDER.index(grade), grade)
    except ValueError:
        return (len(GRADE_ORDER), grade)


def quality_floor_from_payload(payload: dict[str, Any]) -> str | None:
    """The weakest grade the profile rests on, not its best.

    Read from the published payload, which is the same thing a reader is
    served; the floor understates rather than flatters. Ordering is by an
    explicit rank rather than string comparison, under which "A+" sorts
    above "A" and would report the stronger grade as the floor.
    """
    grades: set[str] = set()
    quality = payload.get("quality")
    if isinstance(quality, dict) and isinstance(quality.get("grade"), str):
        grades.add(quality["grade"])
    sections = payload.get("sections")
    if isinstance(sections, dict):
        for statements in sections.values():
            if not isinstance(statements, list):
                continue
            for statement in statements:
                if not isinstance(statement, dict):
                    continue
                details = statement.get("details")
                if isinstance(details, dict) and isinstance(
                    details.get("quality_grade"), str
                ):
                    grades.add(details["quality_grade"])
    if not grades:
        return None
    return max(grades, key=_weakness)


def _review_status(
    session: Session, manifest: PublicationManifest
) -> CoverageReviewStatus:
    """Derive review status from decisions about *this manifest's* evidence.

    ``reviewed`` means a reviewer other than a standing rule selected content
    that the reader is actually served. Scoping to the indexed manifest's
    evidence roots matters: a human may have selected candidate content that
    was never published, and that decision must not upgrade an unrelated
    profile to "reviewed".
    """
    from app.un_wpp import STANDING_ANNUAL_CONTEXT_RULE

    resolved_roots = select(PublicationStatementEvidence.resolved_claim_id).where(
        PublicationStatementEvidence.publication_manifest_id == manifest.id,
        PublicationStatementEvidence.resolved_claim_id.is_not(None),
    )
    derived_roots = select(PublicationStatementEvidence.derived_value_id).where(
        PublicationStatementEvidence.publication_manifest_id == manifest.id,
        PublicationStatementEvidence.derived_value_id.is_not(None),
    )
    reviewers = {
        value.strip()
        for value in session.scalars(
            select(EditorialSelection.reviewed_by).where(
                EditorialSelection.profile_date == manifest.profile_date,
                EditorialSelection.status == EditorialSelectionStatus.SELECTED,
                or_(
                    EditorialSelection.resolved_claim_id.in_(resolved_roots),
                    EditorialSelection.derived_value_id.in_(derived_roots),
                ),
            )
        )
        # reviewed_by is NOT NULL, but nothing forbids an empty string, and
        # "not the standing rule" is not the same as "a person decided".
        if value and value.strip()
    }
    if not reviewers:
        return UNREVIEWED_STATUS
    if reviewers - {STANDING_ANNUAL_CONTEXT_RULE}:
        return REVIEWED_STATUS
    return STANDING_RULE_REVIEW_STATUS


def _current_index_version(session: Session) -> int:
    """The generation the index is already on, so an ordinary publication
    does not silently regress one date to an older one."""
    return session.scalar(select(func.max(CoverageEntry.index_version))) or 1


def upsert_coverage_entry(
    session: Session,
    *,
    manifest: PublicationManifest,
    payload: dict[str, Any] | None = None,
    store: PublishedProfileStore | None = None,
    index_version: int | None = None,
) -> CoverageEntry:
    """Record this date's richness. Called as publication's final step.

    The quality floor comes from the served payload: the publisher passes
    what it just published, and a rebuild reads the artifact when given a
    store. Without either, an existing floor is preserved rather than
    silently nulled.
    """
    if index_version is None:
        index_version = _current_index_version(session)
    counts = _section_counts(session, manifest.id)
    has_event = counts[RECORDED_SECTION] > 0
    entry = session.scalar(
        select(CoverageEntry).where(
            CoverageEntry.profile_date == manifest.profile_date
        )
    )
    if payload is None and store is not None:
        try:
            payload = store.read(manifest.storage_uri, manifest.content_hash)
        except (OSError, RuntimeError, ValueError):
            payload = None
    floor = (
        quality_floor_from_payload(payload)
        if payload is not None
        else (entry.quality_floor if entry is not None else None)
    )
    values = {
        "profile_type": manifest.profile_type,
        "publication_manifest_id": manifest.id,
        "publication_tier": manifest.publication_tier,
        "has_recorded_event": has_event,
        "sections": counts,
        "quality_floor": floor,
        "review_status": _review_status(session, manifest),
        "index_version": index_version,
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
    index_version: int | None = None,
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
    if index_version is None:
        index_version = (
            session.scalar(select(func.max(CoverageEntry.index_version))) or 0
        ) + 1
        session.commit()
    report = CoverageRebuildReport(index_version=index_version)
    snapshot = latest_published_manifests(session)
    session.commit()
    live_dates = set()
    for stale in snapshot:
        with _date_transaction(session, stale.profile_date, stale.profile_type):
            manifest = _latest_published_manifest(session, stale.profile_date)
            if manifest is None:
                # No published manifest with a profile row: not served.
                continue
            if _superseded_by_newer_rebuild(session, stale.profile_date, index_version):
                # A later rebuild already owns this date. Writing our older
                # generation would leave the index reporting two.
                live_dates.add(stale.profile_date)
                continue
            if store is not None and not _artifact_readable(store, manifest):
                # The day endpoint fails for this date. Indexing it anyway
                # would send readers to a page that cannot be served.
                report.unreadable.append(manifest.profile_date)
                continue
            upsert_coverage_entry(
                session, manifest=manifest, store=store, index_version=index_version
            )
            live_dates.add(manifest.profile_date)
    report.dropped = _drop_stale_entries(
        session, live_dates=live_dates, store=store, index_version=index_version
    )
    report.indexed = len(live_dates)
    return report


def _superseded_by_newer_rebuild(
    session: Session, profile_date: date, index_version: int
) -> bool:
    """True when a later rebuild generation already covers this date.

    Two overlapping rebuilds each allocate a generation; without this the
    older one can write its lower generation over the newer one's row, and
    the archive then reports two generations at once.
    """
    existing = session.scalar(
        select(CoverageEntry.index_version).where(
            CoverageEntry.profile_date == profile_date
        )
    )
    return existing is not None and existing > index_version


def _artifact_readable(
    store: PublishedProfileStore, manifest: PublicationManifest
) -> bool:
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
    index_version: int,
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
            if entry.index_version > index_version:
                # Written by a newer rebuild since this run started.
                continue
            manifest = _latest_published_manifest(session, profile_date)
            if manifest is not None and (
                store is None or _artifact_readable(store, manifest)
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
