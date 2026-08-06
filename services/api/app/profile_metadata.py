"""Review status and quality floor, derived in exactly one place (MD1, #45).

These two fields were cut from the coverage index after seven review rounds.
Every finding was real and every fix was correct; the problem was structural.
Both need an editorial join or a quality lookup, and both were threaded
through six writers — publication, idempotent republication, two
reconcile-repair paths, the rebuild, and the migration backfill — so each
writer was a fresh chance for the six to disagree.

So there is one derivation here and every writer delegates to it, including
the migration backfill. Nothing in this module takes the answer as a
parameter; it is always computed from the manifest and the governance
records, which is what makes runtime and backfill agree by construction
rather than by discipline.

Both fields answer a different question from the publication tier. The tier
says how much a profile offers; these say who checked it and how strong its
weakest evidence is. Fusing them is what `reviewed_enriched` did wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.governance import (
    FEATURED_EVENT_SECTION,
    EditorialSelection,
    EditorialSelectionStatus,
    events_behind_manifest,
    is_human_reviewer,
)
from app.models import (
    Claim,
    DerivedValue,
    DerivedValueInput,
    Event,
    PublicationManifest,
    PublicationStatementEvidence,
    QualityAssessment,
    QualityFloor,
    ResolvedClaim,
    ResolvedClaimEvidence,
    ReviewStatus,
    ReviewTask,
)

#: Weakest first. Ordering by rank rather than alphabetically, because a
#: lexicographic max reports "A+" as weaker than "A" — the kind of defect
#: that reads as a quality claim rather than a sorting bug.
_GRADE_RANK: dict[str, int] = {"D": 0, "C": 1, "B": 2, "A": 3}


@dataclass(frozen=True)
class ProfileMetadata:
    review_status: ReviewStatus
    quality_floor: QualityFloor


def _statement_roots(
    session: Session, manifest_id: UUID
) -> tuple[set[tuple[str, UUID]], set[UUID], set[UUID]]:
    """The roots this manifest actually published, keyed for governance.

    Governance keys editorial selections by (profile_date, section_key,
    root), so a decision about the same root in a *different* section is not
    a decision about this statement. Returning the section alongside each
    root is what keeps that distinction available to the caller.
    """
    keyed: set[tuple[str, UUID]] = set()
    resolved_ids: set[UUID] = set()
    derived_ids: set[UUID] = set()
    for row in session.scalars(
        select(PublicationStatementEvidence).where(
            PublicationStatementEvidence.publication_manifest_id == manifest_id
        )
    ):
        section = row.statement_path.split("/")[2] if row.statement_path else ""
        if row.resolved_claim_id is not None:
            keyed.add((section, row.resolved_claim_id))
            resolved_ids.add(row.resolved_claim_id)
        if row.derived_value_id is not None:
            keyed.add((section, row.derived_value_id))
            derived_ids.add(row.derived_value_id)
    return keyed, resolved_ids, derived_ids


def _is_human(reviewer: str | None) -> bool:
    """Whether a decision was recorded by a person.

    Delegates to the one classification rule in `governance`, which every
    standing rule and every governance writer also uses. This module used to
    carry its own copy naming a single rule identity, so each new standing rule
    would have counted as a person until somebody remembered to add it here —
    and the resulting drift reports unreviewed content as reviewed, the single
    most flattering thing this field could get wrong.
    """
    return is_human_reviewer(reviewer)


def _featured_decision_is_human(
    session: Session, *, manifest: PublicationManifest
) -> bool:
    """Whether a person chose this version's headline, per its own binding.

    Only asked of multi-event profiles, where a headline is a real choice. The
    answer comes from the selection row this manifest recorded, not from
    whatever selection is current: an immutable artifact must not be able to
    borrow a human decision made after it was published, which is exactly what
    reading the live selection would allow.

    Fails closed — treating the feature as rule-made — whenever the binding
    cannot be trusted: absent, pointing at a row that no longer exists, naming a
    version other than the one published, or naming an identity the manifest no
    longer publishes. The modest claim is the safe one, and this field's whole
    purpose is to not overstate who checked a page.
    """
    metadata = manifest.metadata_json or {}
    selection_id = metadata.get("featured_event_selection_id")
    selection_version = metadata.get("featured_event_selection_version")
    if not selection_id or selection_version is None:
        return False
    try:
        bound = session.get(EditorialSelection, UUID(str(selection_id)))
    except ValueError:
        return False
    if bound is None or bound.decision_version != selection_version:
        return False
    if bound.status != EditorialSelectionStatus.SELECTED.value:
        return False
    if bound.section_key != FEATURED_EVENT_SECTION:
        return False
    # The featured identity must still be one this version publishes; a binding
    # that names an event the manifest does not carry describes another page.
    published_events = events_behind_manifest(session, manifest=manifest)
    featured_event = session.scalar(
        select(Event).where(Event.resolved_claim_id == bound.resolved_claim_id)
    )
    if featured_event is None or featured_event.id not in published_events:
        return False
    return _is_human(bound.reviewed_by)


def derive_review_status(
    session: Session, *, manifest: PublicationManifest
) -> ReviewStatus:
    """Who or what validated this profile's published content.

    ``human_reviewed`` requires *every* published root to carry a human
    decision. One reviewed statement among six rule-selected ones is not a
    reviewed profile, and reporting it as one would be the same overstatement
    the tier vocabulary was split to remove.

    Derived from recorded review, never from evidence merely existing: a
    profile is not reviewed because it has content.
    """
    keyed, resolved_ids, derived_ids = _statement_roots(session, manifest.id)
    if not keyed:
        return ReviewStatus.AUTOMATED_ONLY

    pending_over = resolved_ids | _claims_behind_derived(session, derived_ids)
    if _has_open_review_task(session, pending_over):
        return ReviewStatus.REVIEW_PENDING

    latest: dict[tuple[str, UUID], EditorialSelection] = {}
    for selection in session.scalars(
        select(EditorialSelection)
        .where(EditorialSelection.profile_date == manifest.profile_date)
        .order_by(EditorialSelection.decision_version.desc())
    ):
        root = selection.resolved_claim_id or selection.derived_value_id
        if root is None:
            continue
        latest.setdefault((selection.section_key, root), selection)

    for key in keyed:
        decision = latest.get(key)
        if (
            decision is None
            or decision.status != EditorialSelectionStatus.SELECTED.value
            or not _is_human(decision.reviewed_by)
        ):
            return ReviewStatus.AUTOMATED_ONLY

    # On a date holding several events, which one leads is part of what the
    # page asserts, and a deterministic rule choosing it is not a person having
    # read the date. Every predicate can be human-selected while the headline
    # was picked by a hash, and calling that reviewed would overstate exactly
    # what this field exists to state honestly.
    if len(events_behind_manifest(session, manifest=manifest)) > 1:
        if not _featured_decision_is_human(session, manifest=manifest):
            return ReviewStatus.AUTOMATED_ONLY
    return ReviewStatus.HUMAN_REVIEWED


def _claims_behind_derived(session: Session, derived_ids: set[UUID]) -> set[UUID]:
    """Resolved claims a derivation was computed from.

    A statement rooted in a derived value has no resolved claim of its own,
    so checking only the direct roots meant an open review task on a
    derivation's inputs could never surface as review_pending — the content
    most likely to be mid-review was the content that could never report it.
    """
    if not derived_ids:
        return set()
    roots = set(
        session.scalars(
            select(DerivedValueInput.resolved_claim_id).where(
                DerivedValueInput.derived_value_id.in_(derived_ids),
                DerivedValueInput.resolved_claim_id.is_not(None),
            )
        )
    )
    # A derivation over other derivations (UC4's comparison) reaches its
    # claims one level further down.
    nested = set(
        session.scalars(
            select(DerivedValueInput.input_derived_value_id).where(
                DerivedValueInput.derived_value_id.in_(derived_ids),
                DerivedValueInput.input_derived_value_id.is_not(None),
            )
        )
    )
    if nested:
        roots |= set(
            session.scalars(
                select(DerivedValueInput.resolved_claim_id).where(
                    DerivedValueInput.derived_value_id.in_(nested),
                    DerivedValueInput.resolved_claim_id.is_not(None),
                )
            )
        )
    # Narrowed after the is_not(None) filters, which the type checker cannot
    # see through.
    return {root for root in roots if root is not None}


def _has_open_review_task(session: Session, resolved_ids: set[UUID]) -> bool:
    if not resolved_ids:
        return False
    claim_ids = set(
        session.scalars(
            select(ResolvedClaimEvidence.claim_id).where(
                ResolvedClaimEvidence.resolved_claim_id.in_(resolved_ids)
            )
        )
    )
    if not claim_ids:
        return False
    return (
        session.scalar(
            select(ReviewTask.id).where(
                ReviewTask.claim_id.in_(claim_ids),
                ReviewTask.status.in_(("open", "in_progress")),
            )
        )
        is not None
    )


def derive_quality_floor(
    session: Session, *, manifest: PublicationManifest
) -> QualityFloor:
    """The weakest graded evidence among this profile's published content.

    A grade outside the known ranking yields ``not_assessed`` rather than a
    letter. Mapping an unrankable grade to "D" would claim a bad grade we
    never measured, and mapping it to "A" would flatter; saying we cannot
    state a floor is the only honest answer, and it is also the weakest
    claim available.
    """
    _, resolved_ids, derived_ids = _statement_roots(session, manifest.id)
    grades = _applicable_grades(session, resolved_ids, derived_ids)
    if not grades:
        return QualityFloor.NOT_ASSESSED

    ranks: list[int] = []
    for grade in grades:
        normalized = (grade or "").strip().upper()
        if normalized not in _GRADE_RANK:
            # Unknown at any point makes the floor unknowable: the weakest
            # item is at best the weakest letter seen and possibly worse.
            return QualityFloor.NOT_ASSESSED
        ranks.append(_GRADE_RANK[normalized])

    weakest = min(ranks)
    for letter, rank in _GRADE_RANK.items():
        if rank == weakest:
            return QualityFloor(letter)
    return QualityFloor.NOT_ASSESSED


def _applicable_grades(
    session: Session, resolved_ids: set[UUID], derived_ids: set[UUID]
) -> list[str]:
    """Every grade bearing on this profile's published evidence.

    QualityAssessment can target a claim, an observation, a derived value or
    a methodology as well as a source release. Reading only release-level
    rows let a D on a published derived value be skipped while an A on its
    release set the floor — the floor reporting the strongest assessment
    rather than the weakest, which is the one direction it must never fail
    in.
    """
    claim_ids = set(
        session.scalars(
            select(ResolvedClaimEvidence.claim_id).where(
                ResolvedClaimEvidence.resolved_claim_id.in_(resolved_ids)
            )
        )
    ) if resolved_ids else set()
    releases = _releases_behind(session, resolved_ids, derived_ids)

    conditions = []
    if releases:
        conditions.append(QualityAssessment.source_release_id.in_(releases))
    if claim_ids:
        conditions.append(QualityAssessment.claim_id.in_(claim_ids))
    if derived_ids:
        conditions.append(QualityAssessment.derived_value_id.in_(derived_ids))
    if not conditions:
        return []
    return list(
        session.scalars(
            select(QualityAssessment.public_grade).where(or_(*conditions))
        )
    )


def _releases_behind(
    session: Session, resolved_ids: set[UUID], derived_ids: set[UUID]
) -> set[UUID]:
    """Every source release this profile's published roots rest on."""
    releases: set[UUID] = set()
    roots = set(resolved_ids)
    if derived_ids:
        roots |= set(
            session.scalars(
                select(DerivedValue.provenance_resolved_claim_id).where(
                    DerivedValue.id.in_(derived_ids),
                    DerivedValue.provenance_resolved_claim_id.is_not(None),
                )
            )
        )
    if not roots:
        return releases
    releases.update(
        session.scalars(
            select(Claim.source_release_id)
            .join(
                ResolvedClaimEvidence,
                ResolvedClaimEvidence.claim_id == Claim.id,
            )
            .join(
                ResolvedClaim,
                ResolvedClaim.id == ResolvedClaimEvidence.resolved_claim_id,
            )
            .where(ResolvedClaim.id.in_(roots))
        )
    )
    return releases


def derive_profile_metadata(
    session: Session, *, manifest: PublicationManifest
) -> ProfileMetadata:
    """The one call every writer makes.

    Publication, republication, both repair paths, the rebuild and the
    migration backfill all route here, so they cannot drift apart. If this
    is ever inlined or parameterised at a call site, #45's seven rounds
    start again.
    """
    return ProfileMetadata(
        review_status=derive_review_status(session, manifest=manifest),
        quality_floor=derive_quality_floor(session, manifest=manifest),
    )


def profile_dates_with_metadata(session: Session) -> list[date]:
    """Convenience for the backfill: every published date, oldest first."""
    return list(
        session.scalars(
            select(PublicationManifest.profile_date)
            .where(PublicationManifest.status == "published")
            .order_by(PublicationManifest.profile_date.asc())
            .distinct()
        )
    )
