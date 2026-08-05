from __future__ import annotations

import enum
import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.models import (
    Base,
    Claim,
    ClaimAssertionStatus,
    Event,
    EventLocation,
    EventTime,
    LegalReviewStatus,
    PipelineRun,
    PublicationManifest,
    PublicationStatementEvidence,
    QualityCheck,
    ResolvedClaim,
    ResolvedClaimEvidence,
    ReviewTask,
    SourceLineage,
    SourceRelease,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ReviewDecisionValue(str, enum.Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class EditorialSelectionStatus(str, enum.Enum):
    SELECTED = "selected"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class SourceReleaseLicense(Base):
    __tablename__ = "source_release_licenses"

    source_release_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("source_releases.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    license_identifier: Mapped[str] = mapped_column(String(160))
    license_snapshot: Mapped[str] = mapped_column(Text)
    license_snapshot_hash: Mapped[str] = mapped_column(String(64))
    terms_url: Mapped[str] = mapped_column(Text)
    commercial_use_permission: Mapped[bool | None] = mapped_column(Boolean)
    redistribution_permission: Mapped[bool | None] = mapped_column(Boolean)
    derivatives_permission: Mapped[bool | None] = mapped_column(Boolean)
    attribution_required: Mapped[bool | None] = mapped_column(Boolean)
    attribution_text: Mapped[str | None] = mapped_column(Text)
    public_display_permission: Mapped[bool | None] = mapped_column(Boolean)
    raw_download_permission: Mapped[bool | None] = mapped_column(Boolean)
    terms_checked_at: Mapped[date] = mapped_column(Date)
    legal_review_status: Mapped[LegalReviewStatus] = mapped_column(
        Enum(
            LegalReviewStatus,
            name="legal_review_status",
            values_callable=lambda values: [item.value for item in values],
            create_type=False,
        ),
        default=LegalReviewStatus.PENDING,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "license_snapshot_hash ~ '^[0-9a-f]{64}$'",
            name="source_release_license_snapshot_hash",
        ),
        CheckConstraint(
            "attribution_required IS NOT TRUE OR attribution_text IS NOT NULL",
            name="source_release_license_attribution_text",
        ),
    )


class ClaimReviewDecision(Base):
    __tablename__ = "claim_review_decisions"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    claim_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("claims.id", ondelete="RESTRICT")
    )
    decision: Mapped[str] = mapped_column(String(16))
    prior_status: Mapped[ClaimAssertionStatus] = mapped_column(
        Enum(
            ClaimAssertionStatus,
            name="claim_assertion_status",
            values_callable=lambda values: [item.value for item in values],
            create_type=False,
        )
    )
    resulting_status: Mapped[ClaimAssertionStatus] = mapped_column(
        Enum(
            ClaimAssertionStatus,
            name="claim_assertion_status",
            values_callable=lambda values: [item.value for item in values],
            create_type=False,
        )
    )
    rationale: Mapped[str] = mapped_column(Text)
    reviewed_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "decision IN ('accepted','rejected','deferred')",
            name="claim_review_decision_value",
        ),
    )


class EditorialSelection(Base):
    __tablename__ = "editorial_selections"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    profile_date: Mapped[date] = mapped_column(Date)
    section_key: Mapped[str] = mapped_column(String(80))
    resolved_claim_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("resolved_claims.id", ondelete="RESTRICT"),
    )
    derived_value_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("derived_values.id", ondelete="RESTRICT"),
    )
    status: Mapped[str] = mapped_column(String(16))
    decision_version: Mapped[int] = mapped_column(Integer)
    display_rank: Mapped[int | None] = mapped_column(Integer)
    rationale: Mapped[str] = mapped_column(Text)
    reviewed_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "profile_date BETWEEN DATE '1900-01-01' AND DATE '2025-12-31'",
            name="editorial_selection_supported_date",
        ),
        CheckConstraint(
            "num_nonnulls(resolved_claim_id, derived_value_id) = 1",
            name="editorial_selection_one_root",
        ),
        CheckConstraint(
            "status IN ('selected','rejected','deferred')",
            name="editorial_selection_status",
        ),
        Index(
            "editorial_selections_resolved_history",
            "profile_date",
            "section_key",
            "resolved_claim_id",
            "decision_version",
            unique=True,
            postgresql_where=text("resolved_claim_id IS NOT NULL"),
        ),
        Index(
            "editorial_selections_derived_history",
            "profile_date",
            "section_key",
            "derived_value_id",
            "decision_version",
            unique=True,
            postgresql_where=text("derived_value_id IS NOT NULL"),
        ),
    )


@dataclass(frozen=True)
class LicenseInput:
    license_identifier: str
    license_snapshot: str
    terms_url: str
    commercial_use_permission: bool | None
    redistribution_permission: bool | None
    derivatives_permission: bool | None
    attribution_required: bool | None
    attribution_text: str | None
    public_display_permission: bool | None
    raw_download_permission: bool | None
    terms_checked_at: date
    legal_review_status: LegalReviewStatus


def register_release_license(
    session: Session,
    *,
    source_release_id: UUID,
    license_input: LicenseInput,
) -> SourceReleaseLicense:
    existing = session.get(SourceReleaseLicense, source_release_id)
    snapshot_hash = hashlib.sha256(
        license_input.license_snapshot.encode("utf-8")
    ).hexdigest()
    if existing is not None:
        expected: dict[str, Any] = {
            "license_identifier": license_input.license_identifier,
            "license_snapshot": license_input.license_snapshot,
            "license_snapshot_hash": snapshot_hash,
            "terms_url": license_input.terms_url,
            "commercial_use_permission": license_input.commercial_use_permission,
            "redistribution_permission": license_input.redistribution_permission,
            "derivatives_permission": license_input.derivatives_permission,
            "attribution_required": license_input.attribution_required,
            "attribution_text": license_input.attribution_text,
            "public_display_permission": license_input.public_display_permission,
            "raw_download_permission": license_input.raw_download_permission,
            "terms_checked_at": license_input.terms_checked_at,
            "legal_review_status": license_input.legal_review_status,
        }
        if any(getattr(existing, key) != value for key, value in expected.items()):
            raise ValueError("An immutable license snapshot already exists for this release.")
        return existing
    row = SourceReleaseLicense(
        source_release_id=source_release_id,
        license_identifier=license_input.license_identifier,
        license_snapshot=license_input.license_snapshot,
        license_snapshot_hash=snapshot_hash,
        terms_url=license_input.terms_url,
        commercial_use_permission=license_input.commercial_use_permission,
        redistribution_permission=license_input.redistribution_permission,
        derivatives_permission=license_input.derivatives_permission,
        attribution_required=license_input.attribution_required,
        attribution_text=license_input.attribution_text,
        public_display_permission=license_input.public_display_permission,
        raw_download_permission=license_input.raw_download_permission,
        terms_checked_at=license_input.terms_checked_at,
        legal_review_status=license_input.legal_review_status,
    )
    session.add(row)
    session.flush()
    return row


def record_claim_review(
    session: Session,
    *,
    claim: Claim,
    decision: ReviewDecisionValue,
    rationale: str,
    reviewed_by: str,
) -> ClaimReviewDecision:
    # Serialize terminal decisions per claim and re-read the current status
    # under the lock so a stale reviewer cannot append a second terminal
    # decision (issue #4).
    lock_key = f"claim-review:{claim.id}"
    session.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(lock_key, 0)))
    )
    session.refresh(claim)
    if claim.assertion_status not in {
        ClaimAssertionStatus.CANDIDATE,
        ClaimAssertionStatus.IN_REVIEW,
    }:
        raise ValueError("Only candidate or in-review claims can receive a review decision.")
    prior = claim.assertion_status
    resulting = {
        ReviewDecisionValue.ACCEPTED: ClaimAssertionStatus.ACCEPTED,
        ReviewDecisionValue.REJECTED: ClaimAssertionStatus.REJECTED,
        ReviewDecisionValue.DEFERRED: ClaimAssertionStatus.IN_REVIEW,
    }[decision]
    claim.assertion_status = resulting
    if decision != ReviewDecisionValue.DEFERRED:
        tasks = list(
            session.scalars(
                select(ReviewTask).where(
                    ReviewTask.claim_id == claim.id,
                    ReviewTask.status.in_(("open", "in_progress")),
                )
            )
        )
        for task in tasks:
            task.status = (
                "dismissed"
                if decision == ReviewDecisionValue.REJECTED
                else "resolved"
            )
            task.completed_at = _utcnow()
    row = ClaimReviewDecision(
        claim_id=claim.id,
        decision=decision.value,
        prior_status=prior,
        resulting_status=resulting,
        rationale=rationale,
        reviewed_by=reviewed_by,
    )
    session.add(row)
    session.flush()
    return row


def record_editorial_selection(
    session: Session,
    *,
    profile_date: date,
    section_key: str,
    resolved_claim_id: UUID | None = None,
    derived_value_id: UUID | None = None,
    status: EditorialSelectionStatus = EditorialSelectionStatus.SELECTED,
    display_rank: int | None,
    rationale: str,
    reviewed_by: str,
) -> EditorialSelection:
    if (resolved_claim_id is None) == (derived_value_id is None):
        raise ValueError("Editorial selection requires exactly one evidence root.")
    root_kind = "resolved" if resolved_claim_id is not None else "derived"
    root_id = resolved_claim_id or derived_value_id
    lock_key = (
        f"editorial-selection:{profile_date.isoformat()}:{section_key}:"
        f"{root_kind}:{root_id}"
    )
    session.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(lock_key, 0)))
    )
    conditions = [
        EditorialSelection.profile_date == profile_date,
        EditorialSelection.section_key == section_key,
    ]
    conditions.append(
        EditorialSelection.resolved_claim_id == resolved_claim_id
        if resolved_claim_id is not None
        else EditorialSelection.derived_value_id == derived_value_id
    )
    existing = session.scalar(
        select(EditorialSelection)
        .where(*conditions)
        .order_by(EditorialSelection.decision_version.desc())
    )
    if (
        existing is not None
        and existing.status == status.value
        and existing.display_rank == display_rank
        and existing.rationale == rationale
        and existing.reviewed_by == reviewed_by
    ):
        return existing
    row = EditorialSelection(
        profile_date=profile_date,
        section_key=section_key,
        resolved_claim_id=resolved_claim_id,
        derived_value_id=derived_value_id,
        status=status.value,
        decision_version=(
            1 if existing is None else existing.decision_version + 1
        ),
        display_rank=display_rank,
        rationale=rationale,
        reviewed_by=reviewed_by,
    )
    session.add(row)
    session.flush()
    _refresh_indexed_metadata(session, profile_date)
    return row


def _refresh_indexed_metadata(session: Session, profile_date: date) -> None:
    """Keep the coverage index honest about who checked this date.

    Review status is derived at publication, so a decision recorded
    afterwards would otherwise leave the index reporting ``automated_only``
    until somebody happened to rebuild — the interface telling a reader
    nobody had checked a page a reviewer had just checked.

    Only the write path calls this. An identical decision returns early
    above without touching anything, so there is nothing to re-derive.

    Imported inside the function because coverage derives review status from
    this module; the dependency only runs one way at import time.
    """
    from app.coverage import refresh_coverage_metadata

    refresh_coverage_metadata(session, profile_date=profile_date)


def lineage_root_ids(session: Session, release_id: UUID) -> frozenset[UUID]:
    if session.get(SourceRelease, release_id) is None:
        raise ValueError("Source independence requires persisted source releases.")
    parents_by_child: dict[UUID, set[UUID]] = {}
    for edge in session.scalars(select(SourceLineage)):
        parents_by_child.setdefault(edge.child_release_id, set()).add(
            edge.parent_release_id
        )
    roots: set[UUID] = set()
    visiting: set[UUID] = set()

    def visit(current: UUID) -> None:
        if current in visiting:
            raise ValueError("Source lineage contains a cycle.")
        parents = parents_by_child.get(current)
        if not parents:
            roots.add(current)
            return
        visiting.add(current)
        for parent in sorted(parents, key=str):
            visit(parent)
        visiting.remove(current)

    visit(release_id)
    return frozenset(roots)


def reviewed_resolutions_for_release(
    session: Session, source_release_id: UUID
) -> dict[str, ResolvedClaim]:
    claims = list(
        session.scalars(
            select(Claim).where(Claim.source_release_id == source_release_id)
        )
    )
    if not claims or any(
        claim.assertion_status != ClaimAssertionStatus.ACCEPTED for claim in claims
    ):
        raise ValueError("Publication requires accepted imported claims.")
    unresolved_tasks = session.scalar(
        select(ReviewTask).where(
            ReviewTask.claim_id.in_([claim.id for claim in claims]),
            ReviewTask.status.in_(("open", "in_progress")),
        )
    )
    if unresolved_tasks is not None:
        raise ValueError("Publication requires completed claim review tasks.")
    rows = list(
        session.execute(
            select(Claim.claim_type, ResolvedClaim)
            .join(
                ResolvedClaimEvidence,
                ResolvedClaimEvidence.claim_id == Claim.id,
            )
            .join(
                ResolvedClaim,
                ResolvedClaim.id == ResolvedClaimEvidence.resolved_claim_id,
            )
            .where(
                Claim.source_release_id == source_release_id,
                ResolvedClaimEvidence.stance == "supporting",
            )
            .order_by(ResolvedClaim.version.desc())
        )
    )
    resolved: dict[str, ResolvedClaim] = {}
    for claim_type, row in rows:
        resolved.setdefault(claim_type, row)
    if set(resolved) != {claim.claim_type for claim in claims}:
        raise ValueError("Publication requires a resolution for every selected claim.")
    return resolved


def assert_release_publication_eligible(
    session: Session,
    *,
    source_release_id: UUID,
    profile_date: date,
    resolved_root_ids_by_section: dict[str, set[UUID]],
    derived_root_ids_by_section: dict[str, set[UUID]] | None = None,
) -> None:
    release = session.get(SourceRelease, source_release_id)
    if release is None:
        raise ValueError("Publication references an unknown source release.")
    license_row = session.get(SourceReleaseLicense, source_release_id)
    if (
        license_row is None
        or license_row.public_display_permission is not True
        or license_row.commercial_use_permission is not True
        or license_row.legal_review_status
        not in {LegalReviewStatus.NOT_REQUIRED, LegalReviewStatus.APPROVED}
    ):
        raise ValueError("Release licensing does not permit public display.")
    run = (
        session.get(PipelineRun, release.pipeline_run_id)
        if release.pipeline_run_id is not None
        else None
    )
    if run is None or run.status != "succeeded":
        raise ValueError("Publication requires a succeeded source pipeline run.")
    checks = list(
        session.scalars(
            select(QualityCheck).where(QualityCheck.pipeline_run_id == run.id)
        )
    )
    if not checks or any(check.status != "passed" for check in checks):
        raise ValueError("Publication requires all recorded quality checks to pass.")
    declared_checks = release.metadata_json.get("required_quality_checks")
    if (
        not isinstance(declared_checks, list)
        or not declared_checks
        or not all(isinstance(name, str) and name for name in declared_checks)
    ):
        raise ValueError("Publication requires adapter-declared quality checks.")
    missing_checks = set(declared_checks) - {check.check_name for check in checks}
    if missing_checks:
        raise ValueError(
            "Publication is missing required quality checks: "
            + ", ".join(sorted(missing_checks))
        )
    latest_by_root: dict[tuple[str, str, UUID], EditorialSelection] = {}
    for selection in session.scalars(
        select(EditorialSelection)
        .where(EditorialSelection.profile_date == profile_date)
        .order_by(EditorialSelection.decision_version.desc())
    ):
        root = (
            ("resolved", selection.resolved_claim_id)
            if selection.resolved_claim_id is not None
            else ("derived", selection.derived_value_id)
        )
        if root[1] is not None:
            latest_by_root.setdefault(
                (selection.section_key, root[0], root[1]), selection
            )
    resolved_selections = {
        (section_key, root_id)
        for (section_key, root_type, root_id), selection in latest_by_root.items()
        if root_type == "resolved"
        and selection.status == EditorialSelectionStatus.SELECTED.value
    }
    derived_selections = {
        (section_key, root_id)
        for (section_key, root_type, root_id), selection in latest_by_root.items()
        if root_type == "derived"
        and selection.status == EditorialSelectionStatus.SELECTED.value
    }
    required_resolved = {
        (section_key, root_id)
        for section_key, root_ids in resolved_root_ids_by_section.items()
        for root_id in root_ids
    }
    required_derived = {
        (section_key, root_id)
        for section_key, root_ids in (derived_root_ids_by_section or {}).items()
        for root_id in root_ids
    }
    if not required_resolved <= resolved_selections or not (
        required_derived <= derived_selections
    ):
        raise ValueError("Publication requires explicit editorial selection for every root.")


# ---------------------------------------------------------------------------
# Event-identity adjudication (Golden 100 / G3a)
# ---------------------------------------------------------------------------

#: Automated editorial identities share this prefix. A standing rule selects
#: content by an accountable, recorded policy (D032) -- it is real editorial
#: provenance, but it is not a person having looked at this date, and must never
#: be reported as one. Classifying by prefix rather than by an enumerated list
#: means a standing rule added later cannot pass as a person merely by being
#: absent from a list somebody forgot to extend.
AUTOMATED_REVIEWER_PREFIX = "standing-rule:"

#: The deterministic featured-event default (G3b) records through this module's
#: writer under this identity. Named here so the adjudication writer can refuse
#: it: a rule may pick a default, but it may never adjudicate identity.
STANDING_FEATURED_EVENT_RULE = "standing-rule:featured-event-v1"

#: Featuring one event among several is a single choice across candidates, not a
#: set of independent per-root yes/no decisions, so it gets its own section key.
FEATURED_EVENT_SECTION = "featured_event"

#: The published section a recorded event occupies.
RECORDED_EVENT_SECTION = "recorded_on_this_date"


def is_human_reviewer(reviewer: str | None) -> bool:
    """Whether a decision was recorded by a person.

    The single classification rule, shared by this module and review status.
    ``derive_review_status`` used to carry its own copy, and two copies of "who
    counts as a person" is one too many: the flattering direction of drift --
    reporting a rule's decision as a human's -- is invisible to a reader.

    A blank or whitespace-only identity is not a person.
    """
    if reviewer is None:
        return False
    identity = reviewer.strip()
    return bool(identity) and not identity.startswith(AUTOMATED_REVIEWER_PREFIX)


class IdentityAdjudicationDecision(str, enum.Enum):
    """A human's answer to "are these two events the same event?"."""

    DISTINCT_EVENT = "distinct_event"
    MERGE = "merge"
    SUPERSEDE = "supersede"
    DEFERRED = "deferred"


#: The decisions that name a surviving event. ``distinct_event`` and ``deferred``
#: leave both events standing, so a survivor on either is a contradiction.
_DIRECTIONAL_DECISIONS = frozenset(
    {IdentityAdjudicationDecision.MERGE, IdentityAdjudicationDecision.SUPERSEDE}
)


class IdentityAdjudicationError(ValueError):
    """A refused identity adjudication."""


class FeaturedEventUnresolved(ValueError):
    """The date's featured event is not exactly one, so nothing may be featured."""


class EventIdentityAdjudication(Base):
    """One versioned human decision about whether two events are the same event.

    Identifies canonical events, never publication manifests: a manifest is a
    versioned publication artifact, so a decision keyed on one would silently stop
    applying the moment the date was republished. The pair is stored canonically
    ordered, which makes the unordered pair unique and rejects self-pairs in the
    same constraint.

    History is append-only. A reviewer who changes their mind adds a version that
    supersedes the previous one; nothing rewrites what was decided before, and the
    foreign keys are ``RESTRICT`` so the audit trail cannot be deleted out from
    under a published decision.
    """

    __tablename__ = "event_identity_adjudications"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_a_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("events.id", ondelete="RESTRICT")
    )
    event_b_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("events.id", ondelete="RESTRICT")
    )
    profile_date: Mapped[date] = mapped_column(Date)
    decision: Mapped[str] = mapped_column(String(16))
    survivor_event_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("events.id", ondelete="RESTRICT")
    )
    decision_version: Mapped[int] = mapped_column(Integer)
    supersedes_adjudication_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("event_identity_adjudications.id", ondelete="RESTRICT"),
    )
    reviewer: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text)
    review_task_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("review_tasks.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    __table_args__ = (
        # Canonical ordering and the self-pair rejection are the same rule: a pair
        # is unordered, and an event is not a pair with itself.
        CheckConstraint(
            "event_a_id < event_b_id",
            name="event_identity_adjudication_canonical_pair",
        ),
        CheckConstraint(
            "decision IN ('distinct_event','merge','supersede','deferred')",
            name="event_identity_adjudication_decision",
        ),
        CheckConstraint(
            "(decision IN ('merge','supersede')) = (survivor_event_id IS NOT NULL)",
            name="event_identity_adjudication_survivor_required",
        ),
        CheckConstraint(
            "survivor_event_id IS NULL "
            "OR survivor_event_id IN (event_a_id, event_b_id)",
            name="event_identity_adjudication_survivor_in_pair",
        ),
        CheckConstraint(
            "btrim(reviewer) <> ''",
            name="event_identity_adjudication_reviewer_present",
        ),
        CheckConstraint(
            "decision_version >= 1",
            name="event_identity_adjudication_version",
        ),
        Index(
            "event_identity_adjudication_history",
            "event_a_id",
            "event_b_id",
            "decision_version",
            unique=True,
        ),
    )


def _canonical_pair(event_a_id: UUID, event_b_id: UUID) -> tuple[UUID, UUID]:
    """The pair in the stored order, matching PostgreSQL's uuid comparison.

    Ordering on the raw bytes rather than the rendered string keeps Python and
    the ``event_a_id < event_b_id`` constraint in agreement.
    """
    first, second = sorted((event_a_id, event_b_id), key=lambda value: value.bytes)
    return first, second


def _primary_occurrence_date(session: Session, event_id: UUID) -> date:
    if session.get(Event, event_id) is None:
        raise IdentityAdjudicationError(
            f"Event {event_id} does not exist, so it cannot be adjudicated."
        )
    event_time = session.scalar(
        select(EventTime).where(
            EventTime.event_id == event_id, EventTime.is_primary.is_(True)
        )
    )
    if event_time is None:
        raise IdentityAdjudicationError(
            f"Event {event_id} has no primary occurrence to adjudicate on."
        )
    return event_time.start_date


def latest_identity_adjudication(
    session: Session, *, event_a_id: UUID, event_b_id: UUID
) -> EventIdentityAdjudication | None:
    """The current decision for a pair, in either argument order."""
    if event_a_id == event_b_id:
        return None
    first, second = _canonical_pair(event_a_id, event_b_id)
    return session.scalars(
        select(EventIdentityAdjudication)
        .where(
            EventIdentityAdjudication.event_a_id == first,
            EventIdentityAdjudication.event_b_id == second,
        )
        .order_by(EventIdentityAdjudication.decision_version.desc())
    ).first()


def record_identity_adjudication(
    session: Session,
    *,
    event_a_id: UUID,
    event_b_id: UUID,
    decision: IdentityAdjudicationDecision,
    reviewer: str,
    rationale: str,
    survivor_event_id: UUID | None = None,
    review_task_id: UUID | None = None,
) -> EventIdentityAdjudication:
    """Record one human decision about whether two events are the same event.

    Human-authored and fail-closed. The date is derived from the two events'
    own primary occurrences rather than taken from the caller, so an adjudication
    cannot claim a date neither event happened on. An identical repeated write
    returns the existing current row; a changed decision appends a version.
    """
    if event_a_id == event_b_id:
        raise IdentityAdjudicationError(
            "An event cannot be adjudicated against itself."
        )
    if not is_human_reviewer(reviewer):
        raise IdentityAdjudicationError(
            "Identity adjudication requires a human reviewer; a pass never "
            "adjudicates on a human's behalf (D038)."
        )
    if decision in _DIRECTIONAL_DECISIONS:
        if survivor_event_id is None:
            raise IdentityAdjudicationError(
                f"A {decision.value} decision requires the surviving event."
            )
        if survivor_event_id not in {event_a_id, event_b_id}:
            raise IdentityAdjudicationError(
                "The surviving event must be one of the adjudicated pair."
            )
    elif survivor_event_id is not None:
        raise IdentityAdjudicationError(
            f"A {decision.value} decision leaves both events standing and "
            "cannot name a survivor."
        )

    first, second = _canonical_pair(event_a_id, event_b_id)
    first_date = _primary_occurrence_date(session, first)
    second_date = _primary_occurrence_date(session, second)
    if first_date != second_date:
        raise IdentityAdjudicationError(
            "Only events that occur on the same date can be adjudicated "
            f"({first_date.isoformat()} vs {second_date.isoformat()})."
        )

    # Serialize per pair so two reviewers cannot mint the same decision_version
    # (the writer pattern the rest of this module uses).
    lock_key = f"identity-adjudication:{first}:{second}"
    session.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(lock_key, 0)))
    )
    current = session.scalars(
        select(EventIdentityAdjudication)
        .where(
            EventIdentityAdjudication.event_a_id == first,
            EventIdentityAdjudication.event_b_id == second,
        )
        .order_by(EventIdentityAdjudication.decision_version.desc())
    ).first()
    if current is not None and (
        current.decision == decision.value
        and current.survivor_event_id == survivor_event_id
        and current.reviewer == reviewer
        and current.rationale == rationale
        and current.review_task_id == review_task_id
    ):
        return current
    row = EventIdentityAdjudication(
        event_a_id=first,
        event_b_id=second,
        profile_date=first_date,
        decision=decision.value,
        survivor_event_id=survivor_event_id,
        decision_version=1 if current is None else current.decision_version + 1,
        supersedes_adjudication_id=None if current is None else current.id,
        reviewer=reviewer,
        rationale=rationale,
        review_task_id=review_task_id,
    )
    session.add(row)
    session.flush()
    return row


def adjudicated_distinct(
    session: Session, *, event_a_id: UUID, event_b_id: UUID
) -> bool:
    """Whether a human has ruled this exact pair to be two different events.

    The only condition under which the recorded-event collision guard lets a
    second event publish on a date. Everything else fails closed: no record, a
    superseded ``distinct_event``, a ``merge``/``supersede``/``deferred``
    outcome, a non-human author, or a decision about a different pair.
    """
    current = latest_identity_adjudication(
        session, event_a_id=event_a_id, event_b_id=event_b_id
    )
    return (
        current is not None
        and current.decision == IdentityAdjudicationDecision.DISTINCT_EVENT.value
        and is_human_reviewer(current.reviewer)
    )


class PublicationRecordedEvent(Base):
    """A canonical event a published version admitted to its recorded section.

    The manifest's own memory of which events it published, and which one it
    featured. Without it the admitted set has to be inferred from whichever
    statement roots happen to resolve to an event, and a non-featured event
    whose statements are less structured simply disappears -- taking its identity
    with it, so a later candidate could be cleared against a date it was never
    judged against.

    ``featured_selection_id`` pins the exact editorial decision this version
    published under, not merely the winning root: a later decision must not be
    able to change what an immutable artifact is understood to have claimed.
    """

    __tablename__ = "publication_recorded_events"

    publication_manifest_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("publication_manifests.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("events.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False)
    featured_selection_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("editorial_selections.id", ondelete="RESTRICT"),
    )
    display_order: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "display_order >= 0", name="publication_recorded_event_order"
        ),
        CheckConstraint(
            "is_featured OR featured_selection_id IS NULL",
            name="publication_recorded_event_selection_on_featured",
        ),
        # A version has one headline. Enforced here rather than trusted to the
        # writer, because "which event did this artifact lead with" is a claim
        # the archive makes to a reader.
        Index(
            "publication_recorded_events_one_featured",
            "publication_manifest_id",
            unique=True,
            postgresql_where=text("is_featured"),
        ),
    )


def record_published_events(
    session: Session,
    *,
    manifest: PublicationManifest,
    event_ids: Sequence[UUID],
    featured_event_id: UUID,
    featured_selection_id: UUID | None,
) -> None:
    """Bind the admitted event set, and the headline, to one published version.

    Idempotent: republishing identical content returns the same manifest by
    content hash, and re-binding it must not fail or duplicate.
    """
    if featured_event_id not in set(event_ids):
        raise ValueError(
            "The featured event must be one of the events this version publishes."
        )
    existing = set(
        session.scalars(
            select(PublicationRecordedEvent.event_id).where(
                PublicationRecordedEvent.publication_manifest_id == manifest.id
            )
        )
    )
    for order, event_id in enumerate(event_ids):
        if event_id in existing:
            continue
        session.add(
            PublicationRecordedEvent(
                publication_manifest_id=manifest.id,
                event_id=event_id,
                is_featured=event_id == featured_event_id,
                featured_selection_id=(
                    featured_selection_id if event_id == featured_event_id else None
                ),
                display_order=order,
            )
        )
    session.flush()


def events_behind_manifest(
    session: Session, *, manifest: PublicationManifest
) -> set[UUID]:
    """The canonical events a manifest's recorded section actually rests on.

    Derived from the evidence graph rather than a stored manifest-to-event
    pointer, because an adjudication is about events and a manifest is a
    versioned artifact: republishing the same event must resolve to the same
    event, and it does, since the new version carries the same roots.

    Both publishers root a recorded statement on the occurrence resolution that
    is also the event's primary ``EventTime`` provenance, and the Wikidata
    publisher refuses to publish without that selection. Identity and location
    provenance are matched too, so a publisher that roots its statements
    differently still resolves.

    A version that recorded its admitted set explicitly is believed over the
    inference: the derivation can only find events whose statements happen to
    root on a relation it knows about, which silently loses a co-published event
    whose statements do not. The inference remains for versions published before
    that binding existed.
    """
    bound = set(
        session.scalars(
            select(PublicationRecordedEvent.event_id).where(
                PublicationRecordedEvent.publication_manifest_id == manifest.id
            )
        )
    )
    if bound:
        return bound
    roots = set(
        session.scalars(
            select(PublicationStatementEvidence.resolved_claim_id).where(
                PublicationStatementEvidence.publication_manifest_id == manifest.id,
                # autoescape, because every underscore in a LIKE pattern is a
                # single-character wildcard: without it `recorded_on_this_date`
                # also matches a section named `recordedXonYthisZdate`, and a
                # lookalike section would resolve to a recorded event.
                PublicationStatementEvidence.statement_path.startswith(
                    f"/sections/{RECORDED_EVENT_SECTION}/", autoescape=True
                ),
                PublicationStatementEvidence.resolved_claim_id.is_not(None),
            )
        )
    )
    if not roots:
        return set()
    found: set[UUID] = set(
        session.scalars(select(Event.id).where(Event.resolved_claim_id.in_(roots)))
    )
    found |= set(
        session.scalars(
            select(EventTime.event_id).where(
                EventTime.provenance_resolved_claim_id.in_(roots)
            )
        )
    )
    found |= set(
        session.scalars(
            select(EventLocation.event_id).where(
                EventLocation.provenance_resolved_claim_id.in_(roots)
            )
        )
    )
    return found


def _latest_featured_selections(
    session: Session, *, profile_date: date
) -> dict[UUID, EditorialSelection]:
    """Each featured-event root's current decision for a date.

    Per-root latest, never a comparison of ``decision_version`` between roots:
    the counters are independent, so "B is version 2 and A is version 1" says
    nothing about which one is featured.
    """
    latest: dict[UUID, EditorialSelection] = {}
    for selection in session.scalars(
        select(EditorialSelection)
        .where(
            EditorialSelection.profile_date == profile_date,
            EditorialSelection.section_key == FEATURED_EVENT_SECTION,
        )
        .order_by(EditorialSelection.decision_version.desc())
    ):
        if selection.resolved_claim_id is not None:
            latest.setdefault(selection.resolved_claim_id, selection)
    return latest


def _validated_candidates(
    session: Session, *, profile_date: date, candidate_root_ids: Sequence[UUID]
) -> list[UUID]:
    """The candidate identity roots, checked to be distinct events on this date."""
    ordered: list[UUID] = []
    for root_id in candidate_root_ids:
        if root_id not in ordered:
            ordered.append(root_id)
    seen_events: set[UUID] = set()
    for root_id in ordered:
        event = session.scalar(
            select(Event).where(Event.resolved_claim_id == root_id)
        )
        if event is None:
            raise FeaturedEventUnresolved(
                f"Featured-event candidate {root_id} is not an event identity root."
            )
        if event.id in seen_events:
            raise FeaturedEventUnresolved(
                "Featured-event candidates must be distinct events."
            )
        seen_events.add(event.id)
        event_time = session.scalar(
            select(EventTime).where(
                EventTime.event_id == event.id, EventTime.is_primary.is_(True)
            )
        )
        if event_time is None or event_time.start_date != profile_date:
            raise FeaturedEventUnresolved(
                f"Featured-event candidate {root_id} does not occur on "
                f"{profile_date.isoformat()}."
            )
    return ordered


def record_featured_event_selection(
    session: Session,
    *,
    profile_date: date,
    candidate_root_ids: Sequence[UUID],
    chosen_root_id: UUID,
    reviewer: str,
    rationale: str,
) -> EditorialSelection:
    """Feature exactly one of a date's events, in one transaction, under one lock.

    Takes the *complete* eligible candidate set rather than a single root, because
    featuring is a choice among candidates: recording only the winner would leave
    the previous winner selected on its own version counter, and both would read
    as featured. Every candidate that is not chosen is rejected here, and a root
    that was selected but has dropped out of the eligible set is rejected too --
    silently leaving it selected would let a withdrawn event keep the headline.

    Idempotent, and D038-safe: a standing rule cannot displace a human's choice.
    """
    lock_key = f"featured-event:{profile_date.isoformat()}"
    session.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(lock_key, 0)))
    )
    candidates = _validated_candidates(
        session, profile_date=profile_date, candidate_root_ids=candidate_root_ids
    )
    if chosen_root_id not in candidates:
        raise FeaturedEventUnresolved(
            "The featured event must be one of the eligible candidates."
        )

    current = _latest_featured_selections(session, profile_date=profile_date)
    # Only a choice that is still on the ballot binds. D038 protects the decision
    # a person made among the events they were choosing between; once their pick
    # is no longer eligible they chose from a set that no longer exists, and
    # treating it as binding would leave the withdrawn event selected, lock the
    # rule out of the current candidates, and fail the resolver closed on a date
    # with perfectly good events to feature.
    human_choice = next(
        (
            root_id
            for root_id in candidates
            if (selection := current.get(root_id)) is not None
            and selection.status == EditorialSelectionStatus.SELECTED.value
            and is_human_reviewer(selection.reviewed_by)
        ),
        None,
    )
    if human_choice is not None and not is_human_reviewer(reviewer):
        # D038: a pass never overrules a human. The rule's default only applies
        # where no person has chosen.
        return current[human_choice]

    chosen_row: EditorialSelection | None = None
    for root_id in candidates:
        selection = record_editorial_selection(
            session,
            profile_date=profile_date,
            section_key=FEATURED_EVENT_SECTION,
            resolved_claim_id=root_id,
            status=(
                EditorialSelectionStatus.SELECTED
                if root_id == chosen_root_id
                else EditorialSelectionStatus.REJECTED
            ),
            display_rank=None,
            rationale=rationale,
            reviewed_by=reviewer,
        )
        if root_id == chosen_root_id:
            chosen_row = selection
    for root_id, selection in current.items():
        if (
            root_id not in candidates
            and selection.status == EditorialSelectionStatus.SELECTED.value
        ):
            record_editorial_selection(
                session,
                profile_date=profile_date,
                section_key=FEATURED_EVENT_SECTION,
                resolved_claim_id=root_id,
                status=EditorialSelectionStatus.REJECTED,
                display_rank=None,
                rationale=(
                    "No longer an eligible featured-event candidate for this date."
                ),
                reviewed_by=reviewer,
            )
    if chosen_row is None:  # pragma: no cover - guarded by the membership check
        raise FeaturedEventUnresolved("The featured event was not recorded.")
    return chosen_row


def current_featured_selection(
    session: Session, *, profile_date: date, root_id: UUID
) -> EditorialSelection | None:
    """The exact featured-event decision currently standing for one root.

    Publication binds this row, not just the root it names, so an artifact's
    recorded provenance cannot be re-read against a decision made after it.
    """
    selection = _latest_featured_selections(
        session, profile_date=profile_date
    ).get(root_id)
    if (
        selection is None
        or selection.status != EditorialSelectionStatus.SELECTED.value
    ):
        return None
    return selection


def resolve_featured_event(
    session: Session,
    *,
    profile_date: date,
    candidate_root_ids: Sequence[UUID],
) -> UUID | None:
    """The one event identity root featured on a date, or None when there is none.

    Zero candidates means the date has no recorded event to feature and the
    caller handles the absence. One candidate is not a choice, so it is returned
    without requiring -- or manufacturing -- an editorial decision. Beyond that,
    exactly one current selection is required: zero or several fail closed rather
    than let query order pick the headline.
    """
    candidates: list[UUID] = []
    for root_id in candidate_root_ids:
        if root_id not in candidates:
            candidates.append(root_id)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    current = _latest_featured_selections(session, profile_date=profile_date)
    selected = [
        root_id
        for root_id in candidates
        if (selection := current.get(root_id)) is not None
        and selection.status == EditorialSelectionStatus.SELECTED.value
    ]
    if len(selected) != 1:
        raise FeaturedEventUnresolved(
            f"{profile_date.isoformat()} has {len(selected)} featured-event "
            f"selections among {len(candidates)} candidates; exactly one is required."
        )
    return selected[0]
