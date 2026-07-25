"""Coverage index (epic #32, slice AA3).

Once every supported date carries annual context, a boolean "published"
flag stops being useful: the archive needs to say how rich each date is,
whether it holds a recorded event, and where the nearest date worth
travelling to lies. The index is derived from published manifests and their
immutable statement evidence — never from prose, and never from assumption.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.governance import EditorialSelection, EditorialSelectionStatus
from app.models import (
    CoverageEntry,
    DayProfile,
    ProfileType,
    PublicationManifest,
    PublicationStatementEvidence,
    PublicationStatus,
    PublicationTier,
)
from app.services import PublishedProfileStore

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


@dataclass(frozen=True)
class CoverageRecord:
    profile_date: date
    profile_type: ProfileType
    publication_tier: PublicationTier
    has_recorded_event: bool
    sections: dict[str, int]
    quality_floor: str | None
    review_status: CoverageReviewStatus
    index_version: int
    nearest_enriched_before: date | None = None
    nearest_enriched_after: date | None = None
    nearest_recorded_event_before: date | None = None
    nearest_recorded_event_after: date | None = None


@dataclass
class CoverageRebuildReport:
    """What a rebuild indexed, and what it refused to index."""

    indexed: int = 0
    dropped: int = 0
    index_version: int = 1
    unreadable: list[date] = field(default_factory=list)


@dataclass
class CoverageSummary:
    total_published: int = 0
    by_tier: dict[str, int] = field(default_factory=dict)
    with_recorded_event: int = 0
    earliest: date | None = None
    latest: date | None = None
    index_version: int = 0


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


def quality_floor_from_payload(payload: dict[str, Any]) -> str | None:
    """The weakest grade the profile rests on, not its best.

    Read from the published payload, which is the same thing a reader is
    served; the floor understates rather than flatters.
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
    return max(grades)


def _review_status(session: Session, profile_date: date) -> CoverageReviewStatus:
    """Derive review status from recorded editorial decisions.

    ``reviewed`` means a reviewer other than the standing rule selected
    content for this date. Everything else says so plainly rather than
    borrowing the credibility of a review that never happened.
    """
    from app.un_wpp import STANDING_ANNUAL_CONTEXT_RULE

    reviewers = {
        value.strip()
        for value in session.scalars(
            select(EditorialSelection.reviewed_by).where(
                EditorialSelection.profile_date == profile_date,
                EditorialSelection.status == EditorialSelectionStatus.SELECTED,
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
        "review_status": _review_status(session, manifest.profile_date),
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

    Each date is re-read under its publication lock, so a correction that
    lands while this walks its snapshot wins instead of being overwritten by
    the manifest that was newest when the walk began.
    """
    from app.services import _acquire_publication_lock

    if index_version is None:
        index_version = (
            session.scalar(select(func.max(CoverageEntry.index_version))) or 0
        ) + 1
    report = CoverageRebuildReport(index_version=index_version)
    snapshot = latest_published_manifests(session)
    live_dates = set()
    for stale in snapshot:
        _acquire_publication_lock(session, stale.profile_date, stale.profile_type)
        manifest = _latest_published_manifest(session, stale.profile_date)
        if manifest is None:
            continue
        has_profile = session.scalar(
            select(DayProfile.id).where(
                DayProfile.publication_manifest_id == manifest.id
            )
        )
        if has_profile is None:
            # A manifest without its profile row is not served to readers.
            continue
        if store is not None:
            try:
                store.read(manifest.storage_uri, manifest.content_hash)
            except (OSError, RuntimeError, ValueError):
                # The day endpoint fails for this date. Indexing it anyway
                # would send readers to a page that cannot be served.
                report.unreadable.append(manifest.profile_date)
                continue
        upsert_coverage_entry(
            session, manifest=manifest, store=store, index_version=index_version
        )
        live_dates.add(manifest.profile_date)
    for entry in session.scalars(select(CoverageEntry)):
        if entry.profile_date not in live_dates:
            session.delete(entry)
            report.dropped += 1
    session.flush()
    report.indexed = len(live_dates)
    return report


def _latest_published_manifest(
    session: Session, profile_date: date
) -> PublicationManifest | None:
    return session.scalar(
        select(PublicationManifest)
        .where(
            PublicationManifest.profile_date == profile_date,
            PublicationManifest.status == PublicationStatus.PUBLISHED,
        )
        .order_by(PublicationManifest.version.desc())
        .limit(1)
    )


def _neighbour(
    session: Session,
    *,
    profile_date: date,
    after: bool,
    require_recorded_event: bool,
) -> date | None:
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
        CoverageEntry.profile_date.asc() if after else CoverageEntry.profile_date.desc()
    ).limit(1)
    return session.scalar(statement)


def coverage_for_date(session: Session, profile_date: date) -> CoverageRecord | None:
    entry = session.scalar(
        select(CoverageEntry).where(CoverageEntry.profile_date == profile_date)
    )
    if entry is None:
        return None
    return CoverageRecord(
        profile_date=entry.profile_date,
        profile_type=entry.profile_type,
        publication_tier=entry.publication_tier,
        has_recorded_event=entry.has_recorded_event,
        sections=dict(entry.sections or {}),
        quality_floor=entry.quality_floor,
        review_status=as_review_status(entry.review_status),
        index_version=entry.index_version,
        nearest_enriched_before=_neighbour(
            session,
            profile_date=profile_date,
            after=False,
            require_recorded_event=False,
        ),
        nearest_enriched_after=_neighbour(
            session, profile_date=profile_date, after=True, require_recorded_event=False
        ),
        nearest_recorded_event_before=_neighbour(
            session, profile_date=profile_date, after=False, require_recorded_event=True
        ),
        nearest_recorded_event_after=_neighbour(
            session, profile_date=profile_date, after=True, require_recorded_event=True
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
            func.max(CoverageEntry.index_version),
        )
    ).one()
    summary.total_published = totals[0] or 0
    summary.with_recorded_event = totals[1] or 0
    summary.earliest = totals[2]
    summary.latest = totals[3]
    summary.index_version = totals[4] or 0
    for tier, count in session.execute(
        select(
            CoverageEntry.publication_tier,
            func.count(CoverageEntry.profile_date),
        ).group_by(CoverageEntry.publication_tier)
    ):
        summary.by_tier[tier.value] = count
    return summary
