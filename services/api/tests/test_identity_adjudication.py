"""Durable distinct-event adjudication and single-choice featured selection (G3a).

A recorded-event collision opened a generic ``ReviewTask`` asking a
human to choose merge, supersede, or distinct-event -- and had nowhere to put the
answer. The collision guard could not read "these two are distinct", so a human
closing the task changed nothing: the next publish attempt collided and deferred
again. The adjudication recorded here is the durable, versioned, pair-specific
answer the guard consumes.

It is fail-closed in every other direction: no record, a malformed one, a
non-human one, a superseded one, or one about a different pair all leave the
collision deferred.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from app.governance import (
    EventIdentityAdjudication,
    IdentityAdjudicationDecision,
    IdentityAdjudicationError,
    adjudicated_distinct,
    events_behind_manifest,
    is_human_reviewer,
    latest_identity_adjudication,
    record_identity_adjudication,
)
from app.models import (
    ClaimAssertionStatus,
    DataStatus,
    DateRole,
    DayProfile,
    Event,
    EventTime,
    LegalReviewStatus,
    ProfileType,
    PublicationManifest,
    ResolutionMethod,
    Source,
    TemporalAssignment,
    TemporalPrecision,
)
from app.services import (
    LocalFilesystemPublishedProfileStore,
    PublicationStatementEvidenceInput,
    create_claim,
    create_source_release,
    publish_day_profile,
    resolve_claim,
)

PROFILE_DATE = date(1964, 3, 27)
OTHER_DATE = date(1970, 1, 1)
HUMAN = "test-human"


def _source(session: Session) -> Source:
    existing = session.scalar(
        select(Source).where(Source.slug == "adjudication-fixture")
    )
    if existing is not None:
        return existing
    source = Source(
        slug="adjudication-fixture",
        name="Adjudication fixture source",
        publisher="Day Perspective tests",
        canonical_url="https://example.invalid/adjudication",
        legal_review_status=LegalReviewStatus.NOT_REQUIRED,
    )
    session.add(source)
    session.flush()
    return source


def _make_event(
    session: Session, *, key: str, on_date: date = PROFILE_DATE
) -> Event:
    """A canonical Event with an identity root and a primary occurrence.

    Built directly rather than through a source adapter: this slice is about
    adjudicating between two events that legitimately share a date, and the only
    committed Wikidata entity shares the golden date with USGS, so the second
    event has to be constructed.
    """
    source = _source(session)
    release = create_source_release(
        session,
        source_id=source.id,
        release_label=f"adjudication-{key}",
        source_url=f"https://example.invalid/{key}",
        raw_storage_uri=f"memory://{key}",
        raw_record_count=1,
        raw_bytes=key.encode(),
        legal_review_status=LegalReviewStatus.NOT_REQUIRED,
    )
    session.flush()
    identity_claim = create_claim(
        session,
        source_release_id=release.id,
        source_record_locator=f"https://example.invalid/{key}",
        source_record_hash_sha256="0" * 64,
        claim_type="candidate_event_identity",
        assertion_text=key,
        assertion_json={"value": {"entity_id": key}},
        assertion_status=ClaimAssertionStatus.ACCEPTED,
    )
    occurrence_claim = create_claim(
        session,
        source_release_id=release.id,
        source_record_locator=f"https://example.invalid/{key}",
        source_record_hash_sha256="0" * 64,
        claim_type="candidate_occurrence_date",
        assertion_text=on_date.isoformat(),
        assertion_json={"value": {"time": on_date.isoformat(), "precision": 11}},
        assertion_status=ClaimAssertionStatus.ACCEPTED,
    )
    identity = resolve_claim(
        session,
        canonical_key=f"fixture:{key}:candidate_event_identity",
        resolved_value={"value": {"entity_id": key}},
        rationale="Fixture identity resolution.",
        supporting_claim_ids=[identity_claim.id],
        resolution_method=ResolutionMethod.SINGLE_SOURCE,
    )
    occurrence = resolve_claim(
        session,
        canonical_key=f"fixture:{key}:candidate_occurrence_date",
        resolved_value={"value": {"time": on_date.isoformat(), "precision": 11}},
        rationale="Fixture occurrence resolution.",
        supporting_claim_ids=[occurrence_claim.id],
        resolution_method=ResolutionMethod.SINGLE_SOURCE,
    )
    event = Event(
        resolved_claim_id=identity.id,
        event_type="Q7944",
        canonical_title=f"Fixture event {key}",
        summary=None,
        data_status=DataStatus.REPORTED,
    )
    session.add(event)
    session.flush()
    session.add(
        EventTime(
            event_id=event.id,
            provenance_resolved_claim_id=occurrence.id,
            start_date=on_date,
            end_date=on_date,
            temporal_precision=TemporalPrecision.DAY,
            temporal_assignment=TemporalAssignment.REPORTED,
            date_role=DateRole.OCCURRED,
            is_primary=True,
        )
    )
    session.flush()
    return event


def _distinct(session: Session, a: Event, b: Event) -> EventIdentityAdjudication:
    return record_identity_adjudication(
        session,
        event_a_id=a.id,
        event_b_id=b.id,
        decision=IdentityAdjudicationDecision.DISTINCT_EVENT,
        reviewer=HUMAN,
        rationale="Two genuinely different events that share a date.",
    )


# --------------------------------------------------------------------------
# Pair identity
# --------------------------------------------------------------------------


@pytest.mark.integration
def test_reversed_pair_input_resolves_to_the_same_pair(session: Session) -> None:
    """The pair is symmetric, so argument order cannot create a second record."""
    a = _make_event(session, key="A")
    b = _make_event(session, key="B")

    first = _distinct(session, a, b)
    reversed_read = latest_identity_adjudication(
        session, event_a_id=b.id, event_b_id=a.id
    )

    assert reversed_read is not None
    assert reversed_read.id == first.id
    # An identical write with the arguments reversed is the same decision, not a
    # new version -- otherwise a retry with swapped operands silently forks history.
    again = record_identity_adjudication(
        session,
        event_a_id=b.id,
        event_b_id=a.id,
        decision=IdentityAdjudicationDecision.DISTINCT_EVENT,
        reviewer=HUMAN,
        rationale="Two genuinely different events that share a date.",
    )
    assert again.id == first.id
    assert (
        session.scalar(
            select(func.count()).select_from(EventIdentityAdjudication)
        )
        == 1
    )


@pytest.mark.integration
def test_self_pair_is_rejected(session: Session) -> None:
    a = _make_event(session, key="A")

    with pytest.raises(IdentityAdjudicationError):
        record_identity_adjudication(
            session,
            event_a_id=a.id,
            event_b_id=a.id,
            decision=IdentityAdjudicationDecision.DISTINCT_EVENT,
            reviewer=HUMAN,
            rationale="An event cannot be distinct from itself.",
        )


@pytest.mark.integration
def test_the_pair_must_share_one_profile_date(session: Session) -> None:
    """The date is derived from the events, never trusted from a caller literal."""
    a = _make_event(session, key="A")
    elsewhere = _make_event(session, key="C", on_date=OTHER_DATE)

    with pytest.raises(IdentityAdjudicationError):
        _distinct(session, a, elsewhere)


@pytest.mark.integration
def test_the_derived_profile_date_is_the_events_own_date(session: Session) -> None:
    a = _make_event(session, key="A")
    b = _make_event(session, key="B")

    row = _distinct(session, a, b)

    assert row.profile_date == PROFILE_DATE


# --------------------------------------------------------------------------
# Append-only version history
# --------------------------------------------------------------------------


@pytest.mark.integration
def test_a_changed_decision_appends_a_version_and_leaves_history_intact(
    session: Session,
) -> None:
    a = _make_event(session, key="A")
    b = _make_event(session, key="B")
    first = _distinct(session, a, b)

    second = record_identity_adjudication(
        session,
        event_a_id=a.id,
        event_b_id=b.id,
        decision=IdentityAdjudicationDecision.DEFERRED,
        reviewer=HUMAN,
        rationale="Reopened: the identity match needs more evidence.",
    )

    assert first.decision_version == 1
    assert second.decision_version == 2
    assert second.supersedes_adjudication_id == first.id
    # The superseded row is history, not a mutable cell.
    session.refresh(first)
    assert first.decision == IdentityAdjudicationDecision.DISTINCT_EVENT.value
    latest = latest_identity_adjudication(session, event_a_id=a.id, event_b_id=b.id)
    assert latest is not None and latest.id == second.id


@pytest.mark.integration
@pytest.mark.parametrize(
    ("label", "statement"),
    [
        (
            "update",
            "UPDATE event_identity_adjudications SET decision = 'deferred' "
            "WHERE id = :id",
        ),
        ("delete", "DELETE FROM event_identity_adjudications WHERE id = :id"),
    ],
)
def test_a_recorded_adjudication_cannot_be_rewritten_or_removed(
    session: Session, label: str, statement: str
) -> None:
    """Append-only in the database, not merely in the writer.

    The unique history index stops two rows sharing a (pair, version); it says
    nothing about an UPDATE that rewrites the latest decision in place or a
    DELETE that removes it, and either would silently change what the
    publication guard consumes. Both sides are probed because a trigger that
    catches one and not the other reads exactly like one that catches both.
    """
    a = _make_event(session, key="A")
    b = _make_event(session, key="B")
    row = _distinct(session, a, b)
    session.flush()

    with pytest.raises(DatabaseError), session.begin_nested():
        session.execute(text(statement), {"id": row.id})

    session.refresh(row)
    assert row.decision == IdentityAdjudicationDecision.DISTINCT_EVENT.value
    assert (
        session.scalar(select(func.count()).select_from(EventIdentityAdjudication))
        == 1
    )


# --------------------------------------------------------------------------
# Decision-specific semantics
# --------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "decision",
    [
        IdentityAdjudicationDecision.MERGE,
        IdentityAdjudicationDecision.SUPERSEDE,
    ],
)
def test_directional_decisions_require_a_survivor_from_the_pair(
    session: Session, decision: IdentityAdjudicationDecision
) -> None:
    a = _make_event(session, key="A")
    b = _make_event(session, key="B")
    outsider = _make_event(session, key="C")

    with pytest.raises(IdentityAdjudicationError):
        record_identity_adjudication(
            session,
            event_a_id=a.id,
            event_b_id=b.id,
            decision=decision,
            reviewer=HUMAN,
            rationale="Missing survivor.",
        )
    with pytest.raises(IdentityAdjudicationError):
        record_identity_adjudication(
            session,
            event_a_id=a.id,
            event_b_id=b.id,
            decision=decision,
            reviewer=HUMAN,
            rationale="Survivor outside the pair.",
            survivor_event_id=outsider.id,
        )

    row = record_identity_adjudication(
        session,
        event_a_id=a.id,
        event_b_id=b.id,
        decision=decision,
        reviewer=HUMAN,
        rationale="A survives.",
        survivor_event_id=a.id,
    )
    assert row.survivor_event_id == a.id


@pytest.mark.integration
@pytest.mark.parametrize(
    "decision",
    [
        IdentityAdjudicationDecision.DISTINCT_EVENT,
        IdentityAdjudicationDecision.DEFERRED,
    ],
)
def test_non_directional_decisions_reject_a_survivor(
    session: Session, decision: IdentityAdjudicationDecision
) -> None:
    a = _make_event(session, key="A")
    b = _make_event(session, key="B")

    with pytest.raises(IdentityAdjudicationError):
        record_identity_adjudication(
            session,
            event_a_id=a.id,
            event_b_id=b.id,
            decision=decision,
            reviewer=HUMAN,
            rationale="No survivor belongs here.",
            survivor_event_id=a.id,
        )


# --------------------------------------------------------------------------
# Human authorship
# --------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "reviewer",
    ["", "   ", "standing-rule:annual-context-v1", "standing-rule:featured-event-v1"],
)
def test_blank_and_automated_reviewers_are_rejected(
    session: Session, reviewer: str
) -> None:
    """Authorship is enforced at the writer, not inferred from cheerful text."""
    a = _make_event(session, key="A")
    b = _make_event(session, key="B")

    with pytest.raises(IdentityAdjudicationError):
        record_identity_adjudication(
            session,
            event_a_id=a.id,
            event_b_id=b.id,
            decision=IdentityAdjudicationDecision.DISTINCT_EVENT,
            reviewer=reviewer,
            rationale="A pass must not adjudicate on a human's behalf (D038).",
        )


@pytest.mark.integration
def test_the_human_rule_is_shared_with_review_status() -> None:
    """One rule, so governance and review-status semantics cannot drift apart."""
    from app.profile_metadata import _is_human

    for identity in ("", "   ", None, "standing-rule:annual-context-v1"):
        assert is_human_reviewer(identity) is False
        assert _is_human(identity) is False
    assert is_human_reviewer(HUMAN) is True
    assert _is_human(HUMAN) is True


# --------------------------------------------------------------------------
# What the collision guard is allowed to consume
# --------------------------------------------------------------------------


@pytest.mark.integration
def test_only_a_current_human_distinct_event_bypasses_the_collision(
    session: Session,
) -> None:
    a = _make_event(session, key="A")
    b = _make_event(session, key="B")

    # No record at all: fail closed.
    assert adjudicated_distinct(session, event_a_id=a.id, event_b_id=b.id) is False

    _distinct(session, a, b)
    assert adjudicated_distinct(session, event_a_id=a.id, event_b_id=b.id) is True

    # Superseded by a later human decision: the stale distinct_event stops counting.
    record_identity_adjudication(
        session,
        event_a_id=a.id,
        event_b_id=b.id,
        decision=IdentityAdjudicationDecision.DEFERRED,
        reviewer=HUMAN,
        rationale="Reopened; no longer a settled distinct-event decision.",
    )
    assert adjudicated_distinct(session, event_a_id=a.id, event_b_id=b.id) is False


@pytest.mark.integration
def test_a_republished_manifest_still_resolves_to_the_same_event(
    session: Session, tmp_path: Path
) -> None:
    """Why the pair keys on events and not on manifests.

    A manifest is a versioned publication artifact. A decision keyed on one would
    stop applying the moment the date was republished -- which is exactly when it
    still has to hold, since republication is what an enrichment does.
    """
    a = _make_event(session, key="A")
    occurrence_root = session.scalar(
        select(EventTime.provenance_resolved_claim_id).where(
            EventTime.event_id == a.id, EventTime.is_primary.is_(True)
        )
    )
    assert occurrence_root is not None
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")

    def _publish(
        revision: int, previous: DayProfile | None
    ) -> tuple[DayProfile, PublicationManifest]:
        profile = publish_day_profile(
            session,
            store=store,
            profile_date=PROFILE_DATE,
            profile_type=ProfileType.STANDARD_STATISTICAL,
            payload={
                "schema_version": "1",
                "date": PROFILE_DATE.isoformat(),
                "profile_type": ProfileType.STANDARD_STATISTICAL.value,
                "sections": {
                    "recorded_on_this_date": [
                        {
                            "statement_id": "fixture-recorded-event",
                            "statement": f"Fixture recorded event, revision {revision}.",
                            "details": {},
                            "provenance_note": "development fixture",
                        }
                    ]
                },
                "section_states": {"recorded_on_this_date": {"status": "available"}},
            },
            statement_evidence=[
                PublicationStatementEvidenceInput(
                    statement_path="/sections/recorded_on_this_date/0",
                    resolved_claim_id=occurrence_root,
                )
            ],
            supersedes_manifest_id=(
                None if previous is None else previous.publication_manifest_id
            ),
            supersedes_day_profile_id=None if previous is None else previous.id,
            editorial_revision=revision,
        )
        manifest = session.get(PublicationManifest, profile.publication_manifest_id)
        assert manifest is not None
        return profile, manifest

    first_profile, first = _publish(1, None)
    _, second = _publish(2, first_profile)

    assert first.id != second.id
    assert events_behind_manifest(session, manifest=first) == {a.id}
    assert events_behind_manifest(session, manifest=second) == {a.id}


@pytest.mark.integration
def test_a_lookalike_section_is_not_a_recorded_event(
    session: Session, tmp_path: Path
) -> None:
    """Every underscore in a LIKE pattern is a single-character wildcard.

    A prefix match on ``/sections/recorded_on_this_date/`` also matches
    ``recordedXonYthisZdate`` unless it is escaped -- and a section that merely
    resembles the recorded-event path would then resolve to a canonical event,
    letting a context statement stand in for a collision.
    """
    a = _make_event(session, key="A")
    occurrence_root = session.scalar(
        select(EventTime.provenance_resolved_claim_id).where(
            EventTime.event_id == a.id, EventTime.is_primary.is_(True)
        )
    )
    assert occurrence_root is not None
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")

    profile = publish_day_profile(
        session,
        store=store,
        profile_date=PROFILE_DATE,
        profile_type=ProfileType.STANDARD_STATISTICAL,
        payload={
            "schema_version": "1",
            "date": PROFILE_DATE.isoformat(),
            "profile_type": ProfileType.STANDARD_STATISTICAL.value,
            "sections": {
                "recordedXonYthisZdate": [
                    {
                        "statement_id": "lookalike",
                        "statement": "A section that resembles the recorded path.",
                        "details": {},
                        "provenance_note": "development fixture",
                    }
                ]
            },
            "section_states": {"recordedXonYthisZdate": {"status": "available"}},
        },
        statement_evidence=[
            PublicationStatementEvidenceInput(
                statement_path="/sections/recordedXonYthisZdate/0",
                resolved_claim_id=occurrence_root,
            )
        ],
    )
    manifest = session.get(PublicationManifest, profile.publication_manifest_id)
    assert manifest is not None

    assert events_behind_manifest(session, manifest=manifest) == set()


@pytest.mark.integration
def test_the_bypass_is_pair_specific(session: Session) -> None:
    """A decision about (A,B) must not become blanket permission for the date."""
    a = _make_event(session, key="A")
    b = _make_event(session, key="B")
    c = _make_event(session, key="C")

    _distinct(session, a, b)

    assert adjudicated_distinct(session, event_a_id=a.id, event_b_id=b.id) is True
    assert adjudicated_distinct(session, event_a_id=a.id, event_b_id=c.id) is False
    assert adjudicated_distinct(session, event_a_id=b.id, event_b_id=c.id) is False
