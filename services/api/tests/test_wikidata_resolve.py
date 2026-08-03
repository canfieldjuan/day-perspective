"""Resolve a reviewed Wikidata candidate into a canonical Event (Golden 100 / G2a).

G1 detects date collisions; G2 turns a candidate into a published recorded event.
This first G2 slice is the resolution stage: once a human has accepted the core
candidate claims (D019 -- Wikidata is discovery, not confirmation), turn them into
resolved claims and a canonical Event / EventTime / EventLocation, honestly
derived from the parsed Wikidata values. It does not publish (a later slice does,
gated by G1's collision check).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.base import LocalFilesystemRawSourceStore
from app.governance import ReviewDecisionValue, record_claim_review
from app.models import (
    Claim,
    DataStatus,
    DateRole,
    Event,
    EventLocation,
    EventTime,
    ResolvedClaim,
    TemporalAssignment,
    TemporalPrecision,
)
from app.wikidata import (
    ENTITY_ID,
    ingest_wikidata_candidate,
    resolve_wikidata_event,
)

WIKIDATA_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "fixtures"
    / "wikidata"
    / f"{ENTITY_ID}.json"
)
CORE_CLAIMS = (
    "candidate_event_identity",
    "candidate_event_type",
    "candidate_name",
    "candidate_occurrence_date",
    "candidate_coordinates",
)


def _ingest(session: Session, tmp_path: Path) -> None:
    ingest_wikidata_candidate(
        session,
        fixture_path=WIKIDATA_FIXTURE,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "wikidata-raw"),
    )


def _claim(session: Session, claim_type: str) -> Claim:
    return session.scalars(
        select(Claim).where(Claim.claim_type == claim_type)
    ).one()


def _accept_core(session: Session) -> None:
    for claim_type in CORE_CLAIMS:
        record_claim_review(
            session,
            claim=_claim(session, claim_type),
            decision=ReviewDecisionValue.ACCEPTED,
            rationale="Human-reviewed Wikidata candidate for this test.",
            reviewed_by="test-human",
        )


@pytest.mark.integration
def test_resolve_builds_event_from_reviewed_candidate(
    session: Session, tmp_path: Path
) -> None:
    _ingest(session, tmp_path)
    _accept_core(session)

    event = resolve_wikidata_event(session)

    # Identity, type, and title come from the reviewed candidate.
    assert event.event_type == "Q7944"
    name_json = _claim(session, "candidate_name").assertion_json
    assert name_json is not None
    assert event.canonical_title == name_json["value"]["label"]
    assert event.data_status is DataStatus.REPORTED

    # When: the occurrence date is parsed from P585 (precision 11 -> day), and a
    # secondary source's date is REPORTED, not a DIRECT_RECORD.
    event_time = session.scalars(
        select(EventTime).where(
            EventTime.event_id == event.id, EventTime.is_primary.is_(True)
        )
    ).one()
    assert event_time.start_date == date(1964, 3, 27)
    assert event_time.temporal_precision is TemporalPrecision.DAY
    assert event_time.date_role is DateRole.OCCURRED
    assert event_time.temporal_assignment is TemporalAssignment.REPORTED

    # Where: a bare P625 point, with no invented named region.
    location = session.scalars(
        select(EventLocation).where(EventLocation.event_id == event.id)
    ).one()
    assert location.geography_version_id is None
    longitude, latitude = session.execute(
        select(
            func.ST_X(EventLocation.point_geometry),
            func.ST_Y(EventLocation.point_geometry),
        ).where(EventLocation.id == location.id)
    ).one()
    assert round(longitude, 2) == -147.65
    assert round(latitude, 2) == 61.02

    # Provenance: every resolved claim is keyed to the Wikidata entity.
    keys = set(
        session.scalars(
            select(ResolvedClaim.canonical_key).where(
                ResolvedClaim.canonical_key.like(f"wikidata:{ENTITY_ID}:%")
            )
        )
    )
    assert f"wikidata:{ENTITY_ID}:candidate_event_identity" in keys
    assert f"wikidata:{ENTITY_ID}:candidate_occurrence_date" in keys


@pytest.mark.integration
def test_resolve_requires_accepted_candidates(
    session: Session, tmp_path: Path
) -> None:
    # D019: Wikidata is candidate discovery, not confirmation. The resolver never
    # auto-accepts -- an unreviewed candidate cannot become a canonical event.
    _ingest(session, tmp_path)

    with pytest.raises(ValueError):
        resolve_wikidata_event(session)

    assert session.scalar(select(func.count()).select_from(Event)) == 0
    assert session.scalar(select(func.count()).select_from(ResolvedClaim)) == 0


@pytest.mark.integration
def test_resolve_is_idempotent(session: Session, tmp_path: Path) -> None:
    _ingest(session, tmp_path)
    _accept_core(session)

    first = resolve_wikidata_event(session)
    second = resolve_wikidata_event(session)

    assert first.id == second.id
    assert session.scalar(select(func.count()).select_from(Event)) == 1


@pytest.mark.integration
def test_resolve_attaches_coordinates_accepted_after_first_resolve(
    session: Session, tmp_path: Path
) -> None:
    # Claims are reviewed independently: the core is accepted and resolved while
    # coordinates are still pending, so the first event has no location.
    _ingest(session, tmp_path)
    for claim_type in (
        "candidate_event_identity",
        "candidate_event_type",
        "candidate_name",
        "candidate_occurrence_date",
    ):
        record_claim_review(
            session,
            claim=_claim(session, claim_type),
            decision=ReviewDecisionValue.ACCEPTED,
            rationale="Reviewed core Wikidata candidate for this test.",
            reviewed_by="test-human",
        )
    event = resolve_wikidata_event(session)
    assert (
        session.scalar(
            select(func.count())
            .select_from(EventLocation)
            .where(EventLocation.event_id == event.id)
        )
        == 0
    )

    # Coordinates reviewed later; re-resolving reconciles the location onto the
    # same event rather than leaving the accepted coordinates permanently absent.
    record_claim_review(
        session,
        claim=_claim(session, "candidate_coordinates"),
        decision=ReviewDecisionValue.ACCEPTED,
        rationale="Reviewed coordinates candidate for this test.",
        reviewed_by="test-human",
    )
    same = resolve_wikidata_event(session)

    assert same.id == event.id
    assert session.scalar(select(func.count()).select_from(Event)) == 1
    location = session.scalars(
        select(EventLocation).where(EventLocation.event_id == event.id)
    ).one()
    assert location.geography_version_id is None
