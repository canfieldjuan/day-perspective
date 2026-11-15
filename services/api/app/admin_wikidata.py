"""Date-bound operator actions over already imported Wikidata candidates."""

from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import wikidata
from app.database import get_session
from app.governance import (
    ReviewDecisionValue,
    record_claim_review,
)
from app.models import (
    Claim,
    ClaimAssertionStatus,
    DateRole,
    Source,
    SourceRelease,
    TemporalPrecision,
    profile_type_for_date,
)

router = APIRouter(prefix="/api/v1/admin/wikidata")
OCCURRENCE = "candidate_occurrence_date"
REVIEWER = "development-review-api"
SessionDep = Annotated[Session, Depends(get_session)]
Rationale = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["accepted", "rejected"]
    rationale: Rationale


@dataclass(frozen=True)
class Subject:
    day: date
    release: SourceRelease
    entity_id: str
    claims: dict[str, Claim]

    @property
    def accepted_core(self) -> bool:
        return all(
            predicate in self.claims
            and self.claims[predicate].assertion_status is ClaimAssertionStatus.ACCEPTED
            for predicate in wikidata.REQUIRED_EVENT_CLAIMS
        )


def _validate_day(day: date) -> None:
    if profile_type_for_date(day) is None:
        raise HTTPException(404, "Date is outside the supported public archive.")


def _candidate(session: Session, release: SourceRelease, day: date) -> Subject:
    claims = {
        claim.claim_type: claim
        for claim in session.scalars(
            select(Claim).where(Claim.source_release_id == release.id).order_by(Claim.claim_type)
        )
    }
    occurrence = claims.get(OCCURRENCE)
    if (
        occurrence is None
        or occurrence.temporal_precision is not TemporalPrecision.DAY
        or occurrence.date_role is not DateRole.OCCURRED
        or occurrence.temporal_start != day
        or occurrence.temporal_end != day
        or wikidata._persisted_occurrence(occurrence).profile_date != day
    ):
        raise ValueError(
            "This release has no qualified day-precision occurrence on the selected date."
        )
    identity = claims.get("candidate_event_identity")
    entity_id = wikidata._candidate_value(identity).get("entity_id") if identity else None
    if not isinstance(entity_id, str):
        raise ValueError("The selected release has no Wikidata entity identity.")
    return Subject(day, release, entity_id, claims)


def _subject(profile_date: date, release_id: UUID, session: SessionDep) -> Subject:
    _validate_day(profile_date)
    try:
        release = wikidata.select_wikidata_release(session, source_release_id=release_id)
    except ValueError as error:
        raise HTTPException(404, str(error)) from error
    try:
        return _candidate(session, release, profile_date)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


SubjectDep = Annotated[Subject, Depends(_subject)]


def _view(session: Session, subject: Subject) -> dict[str, Any]:
    blocked = None
    event = None
    try:
        _, event = wikidata._bound_resolutions(
            session, release=subject.release, qid=subject.entity_id, claims=subject.claims
        )
    except ValueError as error:
        blocked = str(error)
    identity = wikidata._candidate_value(subject.claims["candidate_event_identity"])
    name = subject.claims.get("candidate_name")
    claims = sorted(
        subject.claims.values(),
        key=lambda claim: (
            claim.claim_type not in wikidata.REQUIRED_EVENT_CLAIMS,
            claim.claim_type,
        ),
    )
    return {
        "release_id": subject.release.id,
        "label": wikidata._candidate_value(name).get("label")
        if name
        else subject.release.release_label,
        "entity_id": subject.entity_id,
        "revision": identity.get("revision_id"),
        "source_url": subject.release.source_url,
        "checksum": subject.release.raw_checksum_sha256,
        "claims": [
            {
                "id": claim.id,
                "predicate": claim.claim_type,
                "status": claim.assertion_status.value,
                "value": claim.assertion_json,
                "locator": claim.source_record_locator,
            }
            for claim in claims
        ],
        "can_resolve": subject.accepted_core and blocked is None,
        "resolved": event is not None,
        "blocked": blocked,
    }


@router.get("/{profile_date}")
def candidates(profile_date: date, session: SessionDep) -> dict[str, Any]:
    _validate_day(profile_date)
    releases = session.scalars(
        select(SourceRelease)
        .join(Source)
        .join(Claim, Claim.source_release_id == SourceRelease.id)
        .where(
            Source.slug == wikidata.WIKIDATA_SOURCE_SLUG,
            Claim.claim_type == OCCURRENCE,
            Claim.temporal_start == profile_date,
            Claim.temporal_precision == TemporalPrecision.DAY,
        )
        .order_by(SourceRelease.id)
    ).unique()
    subjects = []
    for release in releases:
        try:
            subject = _candidate(session, release, profile_date)
        except ValueError:
            continue
        subjects.append(subject)
    # Take the resolution locks in entity order even when two dates contain
    # different revisions of the same entities.
    return {
        "candidates": [
            _view(session, subject) for subject in sorted(subjects, key=lambda row: row.entity_id)
        ]
    }


@router.post("/{profile_date}/{release_id}/claims/{claim_id}/decision")
def decide(
    claim_id: UUID, request: DecisionRequest, subject: SubjectDep, session: SessionDep
) -> dict[str, str]:
    claim = next((claim for claim in subject.claims.values() if claim.id == claim_id), None)
    if claim is None:
        raise HTTPException(404, "Claim does not belong to the selected release.")
    try:
        record_claim_review(
            session,
            claim=claim,
            decision=ReviewDecisionValue(request.decision),
            rationale=request.rationale,
            reviewed_by=REVIEWER,
        )
        session.commit()
    except ValueError as error:
        session.rollback()
        raise HTTPException(409, str(error)) from error
    return {"status": claim.assertion_status.value}


@router.post("/{profile_date}/{release_id}/resolve")
def resolve(subject: SubjectDep, session: SessionDep) -> dict[str, str]:
    try:
        wikidata.resolve_wikidata_event(session, source_release_id=subject.release.id)
        session.commit()
    except ValueError as error:
        session.rollback()
        raise HTTPException(409, str(error)) from error
    return {"status": "resolved"}
