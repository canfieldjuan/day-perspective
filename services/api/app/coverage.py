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
from typing import Any
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
STANDING_RULE_REVIEW_STATUS = "rule_selected"
REVIEWED_STATUS = "reviewed"


@dataclass(frozen=True)
class CoverageRecord:
    profile_date: date
    profile_type: ProfileType
    publication_tier: PublicationTier
    has_recorded_event: bool
    sections: dict[str, int]
    quality_floor: str | None
    review_status: str
    index_version: int
    nearest_enriched_before: date | None = None
    nearest_enriched_after: date | None = None
    nearest_recorded_event_before: date | None = None
    nearest_recorded_event_after: date | None = None


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


def upsert_coverage_entry(
    session: Session,
    *,
    manifest: PublicationManifest,
    payload: dict[str, Any] | None = None,
    store: PublishedProfileStore | None = None,
    index_version: int = 1,
) -> CoverageEntry:
    """Record this date's richness. Called as publication's final step.

    The quality floor comes from the served payload: the publisher passes
    what it just published, and a rebuild reads the artifact when given a
    store. Without either, an existing floor is preserved rather than
    silently nulled.
    """
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
        "review_status": (
            REVIEWED_STATUS if has_event else STANDING_RULE_REVIEW_STATUS
        ),
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
) -> int:
    """Regenerate the whole index from published state.

    Deterministic and idempotent: dates whose newest publication disappeared
    or was superseded by an unpublished manifest are dropped rather than
    left describing an archive that no longer exists.
    """
    if index_version is None:
        index_version = (
            session.scalar(select(func.max(CoverageEntry.index_version))) or 0
        ) + 1
    manifests = latest_published_manifests(session)
    live_dates = set()
    for manifest in manifests:
        has_profile = session.scalar(
            select(DayProfile.id).where(
                DayProfile.publication_manifest_id == manifest.id
            )
        )
        if has_profile is None:
            # A manifest without its profile row is not served to readers.
            continue
        upsert_coverage_entry(
            session, manifest=manifest, store=store, index_version=index_version
        )
        live_dates.add(manifest.profile_date)
    for entry in session.scalars(select(CoverageEntry)):
        if entry.profile_date not in live_dates:
            session.delete(entry)
    session.flush()
    return len(live_dates)


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
        review_status=entry.review_status,
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
    summary = CoverageSummary()
    summary.by_tier = dict.fromkeys((tier.value for tier in PublicationTier), 0)
    for entry in session.scalars(select(CoverageEntry)):
        summary.total_published += 1
        summary.by_tier[entry.publication_tier.value] += 1
        if entry.has_recorded_event:
            summary.with_recorded_event += 1
        summary.earliest = (
            entry.profile_date
            if summary.earliest is None
            else min(summary.earliest, entry.profile_date)
        )
        summary.latest = (
            entry.profile_date
            if summary.latest is None
            else max(summary.latest, entry.profile_date)
        )
        summary.index_version = max(summary.index_version, entry.index_version)
    return summary
