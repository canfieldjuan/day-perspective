from __future__ import annotations

import enum
import hashlib
import uuid
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
    Integer,
    String,
    Text,
    select,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.models import (
    Base,
    Claim,
    ClaimAssertionStatus,
    LegalReviewStatus,
    PipelineRun,
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
    return row


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
