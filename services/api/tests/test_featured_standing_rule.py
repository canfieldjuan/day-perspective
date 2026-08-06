"""The deterministic featured-event default, and what happens as the set changes.

G3a made featuring one of a date's events a single choice a human can record.
G3b-1 made publication keep every admitted event. Neither supplies a default, so
a multi-event date with no human choice fails closed — correct, but it means the
G4 operator pass would have to hand-pick a headline for every date that happens
to hold two events.

This is the rule that fills that gap, and the tests below are mostly about its
*lifecycle* rather than its arithmetic. The tiebreak itself is deliberately
uninteresting:

    score = sha256(canonical_json({profile_date, identity_key}))
    featured = the candidate with the smallest score

Canonical JSON rather than a joined string, so an identity key containing the
delimiter cannot make two different candidates score alike.

Not lexicographic `canonical_key` order, which is source-prefixed
(``wikidata:<QID>:...``) and would smuggle in a preference for one source over
another. Not ``Event.created_at``, which would make ingestion order editorial
policy. The rule claims no significance, and must not appear to.

What matters is what the rule does when the world moves underneath it: when the
candidate set grows, when a human has already chosen, and — the case that has
caught me repeatedly — when the human's chosen event stops qualifying at all.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.governance import (
    FEATURED_EVENT_SECTION,
    FEATURED_ORIGIN_HUMAN,
    FEATURED_ORIGIN_STANDING_RULE,
    STANDING_FEATURED_EVENT_RULE,
    EditorialSelection,
    FeaturedEventEvaluation,
    FeaturedEventUnresolved,
    evaluate_featured_event,
    featured_candidate_fingerprint,
    resolve_featured_event,
)
from app.models import Event, ResolvedClaim
from app.services import canonical_json_bytes

from .test_identity_adjudication import HUMAN, PROFILE_DATE, _make_event

STANDING_RULE_VERSION = "featured-event-sha256-v1"


def _root(session: Session, event: Event) -> ResolvedClaim:
    resolved = session.get(ResolvedClaim, event.resolved_claim_id)
    assert resolved is not None
    return resolved


def _expected_winner(session: Session, events: list[Event]) -> Event:
    """The rule recomputed independently, from the specification rather than the code."""

    def score(event: Event) -> str:
        return _score_for(_root(session, event).canonical_key)

    return min(events, key=score)


def _score_for(canonical_key: str) -> str:
    """The tiebreak, restated from the specification rather than imported."""
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "profile_date": PROFILE_DATE.isoformat(),
                "identity_key": canonical_key,
            }
        )
    ).hexdigest()


def _featured_rows(session: Session) -> int | None:
    return session.scalar(
        select(func.count())
        .select_from(EditorialSelection)
        .where(EditorialSelection.section_key == FEATURED_EVENT_SECTION)
    )


def _apply(session: Session, events: list[Event]) -> FeaturedEventEvaluation | None:
    return evaluate_featured_event(
        session,
        profile_date=PROFILE_DATE,
        candidate_root_ids=[event.resolved_claim_id for event in events],
    )


# --------------------------------------------------------------------------
# The rule itself
# --------------------------------------------------------------------------


@pytest.mark.integration
def test_the_rule_picks_the_smallest_sha256_of_date_and_identity_key(
    session: Session, tmp_path: Path
) -> None:
    """Recomputed from the spec, not asserted against a captured value.

    A test that pinned whatever the implementation happened to produce would
    pass just as happily if the rule silently became "first by creation order".
    """
    events = [_make_event(session, key=key) for key in ("alpha", "beta", "gamma")]

    _apply(session, events)

    winner = _expected_winner(session, events)
    assert (
        resolve_featured_event(
            session,
            profile_date=PROFILE_DATE,
            candidate_root_ids=[event.resolved_claim_id for event in events],
        )
        == winner.resolved_claim_id
    )


@pytest.mark.integration
def test_the_rule_does_not_follow_identity_key_order(
    session: Session, tmp_path: Path
) -> None:
    """The tiebreak must not smuggle in a source preference.

    Identity keys are source-prefixed, so a lexicographic rule would
    systematically favour whichever source sorts first — a significance claim
    the archive has no basis for (§12).
    """
    # Keys chosen so the sha256 winner is *not* the lexicographically first
    # identity key. The assertion below guards that separation, and it has
    # already earned its place: it caught the previous fixture the moment the
    # scoring input changed, when the test would otherwise have kept passing
    # while proving nothing.
    events = [_make_event(session, key=key) for key in ("ev0", "ev1", "ev2")]
    by_key = sorted(events, key=lambda event: _root(session, event).canonical_key)
    winner = _expected_winner(session, events)

    assert winner.id != by_key[0].id, (
        "fixture no longer distinguishes sha256 order from key order"
    )

    _apply(session, events)

    assert (
        resolve_featured_event(
            session,
            profile_date=PROFILE_DATE,
            candidate_root_ids=[event.resolved_claim_id for event in events],
        )
        == winner.resolved_claim_id
    )


@pytest.mark.integration
def test_the_rule_records_its_version_fingerprint_and_rationale(
    session: Session, tmp_path: Path
) -> None:
    """A rule-made choice must be auditable as a rule-made choice."""
    events = [_make_event(session, key=key) for key in ("alpha", "beta")]

    _apply(session, events)

    winner = _expected_winner(session, events)
    row = session.scalars(
        select(EditorialSelection)
        .where(
            EditorialSelection.profile_date == PROFILE_DATE,
            EditorialSelection.section_key == FEATURED_EVENT_SECTION,
            EditorialSelection.resolved_claim_id == winner.resolved_claim_id,
        )
        .order_by(EditorialSelection.decision_version.desc())
    ).first()
    assert row is not None
    assert row.reviewed_by == STANDING_FEATURED_EVENT_RULE
    assert STANDING_RULE_VERSION in row.rationale
    assert (
        featured_candidate_fingerprint(
            session,
            profile_date=PROFILE_DATE,
            candidate_root_ids=[event.resolved_claim_id for event in events],
        )
        in row.rationale
    )
    assert _root(session, winner).canonical_key in row.rationale


# --------------------------------------------------------------------------
# Lifecycle: same set
# --------------------------------------------------------------------------


@pytest.mark.integration
def test_the_same_candidate_set_writes_no_new_history(
    session: Session, tmp_path: Path
) -> None:
    """Same candidates, same winner, no new governance rows.

    A rule that appended a decision every time it ran would turn an audit trail
    into a log file, and make ``decision_version`` meaningless.
    """
    events = [_make_event(session, key=key) for key in ("alpha", "beta")]
    _apply(session, events)
    before = _featured_rows(session)

    _apply(session, events)

    assert _featured_rows(session) == before


# --------------------------------------------------------------------------
# Lifecycle: the set grows
# --------------------------------------------------------------------------


@pytest.mark.integration
def test_a_new_candidate_is_scored_against_the_new_set(
    session: Session, tmp_path: Path
) -> None:
    """A + B becomes A + B + C, so the rule runs against A + B + C."""
    first = _make_event(session, key="alpha")
    second = _make_event(session, key="beta")
    _apply(session, [first, second])

    third = _make_event(session, key="gamma")
    _apply(session, [first, second, third])

    winner = _expected_winner(session, [first, second, third])
    assert (
        resolve_featured_event(
            session,
            profile_date=PROFILE_DATE,
            candidate_root_ids=[
                event.resolved_claim_id for event in (first, second, third)
            ],
        )
        == winner.resolved_claim_id
    )


@pytest.mark.integration
def test_a_grown_set_that_keeps_the_same_winner_appends_no_decision(
    session: Session, tmp_path: Path
) -> None:
    """History records changes of mind, not re-evaluations.

    The candidate set changing is not itself a decision; only the winner moving
    is. Otherwise every new event on a date rewrites the headline's history
    without the headline having moved.
    """
    first = _make_event(session, key="alpha")
    second = _make_event(session, key="beta")
    _apply(session, [first, second])
    winner_before = _expected_winner(session, [first, second])

    # Choose a third event that provably does *not* win, rather than skipping
    # when an arbitrary one happens to. A test that skips proves nothing, and
    # this is the case the rule's "no new history" claim rests on.
    losing_key = next(
        key
        for key in (f"late-{index}" for index in range(50))
        if _score_for(f"fixture:{key}:candidate_event_identity")
        > _score_for(_root(session, winner_before).canonical_key)
    )
    third = _make_event(session, key=losing_key)
    assert _expected_winner(session, [first, second, third]).id == winner_before.id
    before = _featured_rows(session)

    _apply(session, [first, second, third])

    assert _featured_rows(session) == before


# --------------------------------------------------------------------------
# Lifecycle: a human has chosen
# --------------------------------------------------------------------------


@pytest.mark.integration
def test_the_rule_does_not_displace_a_human_choice_when_the_set_grows(
    session: Session, tmp_path: Path
) -> None:
    """D038, under the condition that actually threatens it.

    A rule that only checked for a human decision on its first run would quietly
    take the headline back the moment a third event appeared.
    """
    first = _make_event(session, key="alpha")
    second = _make_event(session, key="beta")
    from app.governance import record_featured_event_selection

    record_featured_event_selection(
        session,
        profile_date=PROFILE_DATE,
        candidate_root_ids=[first.resolved_claim_id, second.resolved_claim_id],
        chosen_root_id=second.resolved_claim_id,
        reviewer=HUMAN,
        rationale="A person chose the second event.",
    )

    third = _make_event(session, key="gamma")
    _apply(session, [first, second, third])

    assert (
        resolve_featured_event(
            session,
            profile_date=PROFILE_DATE,
            candidate_root_ids=[
                event.resolved_claim_id for event in (first, second, third)
            ],
        )
        == second.resolved_claim_id
    ), "the standing rule displaced a human's choice"


# --------------------------------------------------------------------------
# Lifecycle: the human's choice stops qualifying — the premise change
# --------------------------------------------------------------------------


@pytest.mark.integration
def test_a_human_choice_that_leaves_the_eligible_set_fails_closed(
    session: Session, tmp_path: Path
) -> None:
    """The premise-change case, and the one worth being most careful about.

    A person chose B. B is no longer eligible. Two tempting behaviours are both
    wrong: protecting B, which features an event the date no longer admits; and
    silently recomputing the rule over the survivors, which replaces a human's
    decision with a machine's and presents it as continuous. The date has no
    honest headline until a person supplies one.
    """
    first = _make_event(session, key="alpha")
    second = _make_event(session, key="beta")
    from app.governance import record_featured_event_selection

    record_featured_event_selection(
        session,
        profile_date=PROFILE_DATE,
        candidate_root_ids=[first.resolved_claim_id, second.resolved_claim_id],
        chosen_root_id=second.resolved_claim_id,
        reviewer=HUMAN,
        rationale="A person chose the second event.",
    )

    third = _make_event(session, key="gamma")
    surviving = [first, third]

    with pytest.raises(FeaturedEventUnresolved, match="no longer eligible"):
        _apply(session, surviving)

    # And nothing was quietly written on the way out.
    rows = session.scalars(
        select(EditorialSelection).where(
            EditorialSelection.section_key == FEATURED_EVENT_SECTION,
            EditorialSelection.reviewed_by == STANDING_FEATURED_EVENT_RULE,
        )
    ).all()
    assert not rows, "the rule wrote a decision while failing closed"


@pytest.mark.integration
def test_a_rule_choice_that_leaves_the_eligible_set_is_simply_recomputed(
    session: Session, tmp_path: Path
) -> None:
    """The other side: only a *human* choice earns the fail-closed treatment.

    The rule superseding its own earlier default is not overruling anybody, so
    it recomputes rather than blocking the date.
    """
    first = _make_event(session, key="alpha")
    second = _make_event(session, key="beta")
    _apply(session, [first, second])
    previous = _expected_winner(session, [first, second])

    third = _make_event(session, key="gamma")
    surviving = [event for event in (first, second, third) if event.id != previous.id]

    _apply(session, surviving)

    winner = _expected_winner(session, surviving)
    assert (
        resolve_featured_event(
            session,
            profile_date=PROFILE_DATE,
            candidate_root_ids=[event.resolved_claim_id for event in surviving],
        )
        == winner.resolved_claim_id
    )


# --------------------------------------------------------------------------
# Boundaries
# --------------------------------------------------------------------------


@pytest.mark.integration
def test_a_single_candidate_needs_no_rule_decision(
    session: Session, tmp_path: Path
) -> None:
    """One event is not a choice, so the rule records nothing."""
    only = _make_event(session, key="alpha")

    _apply(session, [only])

    assert _featured_rows(session) == 0


@pytest.mark.integration
def test_no_candidates_records_nothing(session: Session, tmp_path: Path) -> None:
    evaluate_featured_event(
        session, profile_date=PROFILE_DATE, candidate_root_ids=[]
    )

    assert _featured_rows(session) == 0


@pytest.mark.integration
def test_the_fingerprint_is_order_independent_and_set_specific(
    session: Session, tmp_path: Path
) -> None:
    """The fingerprint identifies a set of candidates, not a listing of them."""
    first = _make_event(session, key="alpha")
    second = _make_event(session, key="beta")
    third = _make_event(session, key="gamma")

    forwards = featured_candidate_fingerprint(
        session,
        profile_date=PROFILE_DATE,
        candidate_root_ids=[first.resolved_claim_id, second.resolved_claim_id],
    )
    backwards = featured_candidate_fingerprint(
        session,
        profile_date=PROFILE_DATE,
        candidate_root_ids=[second.resolved_claim_id, first.resolved_claim_id],
    )
    grown = featured_candidate_fingerprint(
        session,
        profile_date=PROFILE_DATE,
        candidate_root_ids=[
            first.resolved_claim_id,
            second.resolved_claim_id,
            third.resolved_claim_id,
        ],
    )
    other_date = featured_candidate_fingerprint(
        session,
        profile_date=date(1970, 1, 1),
        candidate_root_ids=[first.resolved_claim_id, second.resolved_claim_id],
    )

    assert forwards == backwards
    assert forwards != grown
    assert forwards != other_date


@pytest.mark.integration
def test_a_losing_new_candidate_keeps_the_decision_but_changes_the_fingerprint(
    session: Session, tmp_path: Path
) -> None:
    """The separation the contract turns on.

    A + B selects A at version 1. C arrives and loses. No new decision is
    recorded — the headline has not moved — but the evaluation must report the
    *new* candidate set, because a publication made now considered A + B + C.
    Reusing the older fingerprint would let candidate-set provenance become
    stale decoration attached to a decision that no longer describes it.
    """
    first = _make_event(session, key="alpha")
    second = _make_event(session, key="beta")
    initial = _apply(session, [first, second])
    assert initial is not None
    assert initial.decision_changed is True
    assert initial.selection_version == 1
    assert initial.selection_origin == FEATURED_ORIGIN_STANDING_RULE
    rows_before = _featured_rows(session)

    losing_key = next(
        key
        for key in (f"late-{index}" for index in range(50))
        if _score_for(f"fixture:{key}:candidate_event_identity")
        > _score_for(
            _root(session, _expected_winner(session, [first, second])).canonical_key
        )
    )
    third = _make_event(session, key=losing_key)

    grown = _apply(session, [first, second, third])

    assert grown is not None
    # Same decision: same row, same version, nothing appended.
    assert grown.selection_id == initial.selection_id
    assert grown.selection_version == initial.selection_version
    assert grown.decision_changed is False
    assert _featured_rows(session) == rows_before
    # New provenance: the evaluation describes the set actually considered.
    assert grown.candidate_set_fingerprint != initial.candidate_set_fingerprint
    assert grown.candidate_set_fingerprint == featured_candidate_fingerprint(
        session,
        profile_date=PROFILE_DATE,
        candidate_root_ids=[
            event.resolved_claim_id for event in (first, second, third)
        ],
    )


@pytest.mark.integration
def test_a_human_choice_is_reported_as_human_origin(
    session: Session, tmp_path: Path
) -> None:
    """Review status depends on this, so it is recorded rather than re-inferred."""
    first = _make_event(session, key="alpha")
    second = _make_event(session, key="beta")
    from app.governance import record_featured_event_selection

    chosen = record_featured_event_selection(
        session,
        profile_date=PROFILE_DATE,
        candidate_root_ids=[first.resolved_claim_id, second.resolved_claim_id],
        chosen_root_id=second.resolved_claim_id,
        reviewer=HUMAN,
        rationale="A person chose the second event.",
    )

    evaluation = _apply(session, [first, second])

    assert evaluation is not None
    assert evaluation.selection_origin == FEATURED_ORIGIN_HUMAN
    assert evaluation.selection_id == chosen.id
    assert evaluation.selection_version == chosen.decision_version
    assert evaluation.decision_changed is False


@pytest.mark.integration
def test_the_evaluation_always_describes_its_own_bound_selection(
    session: Session, tmp_path: Path
) -> None:
    """The winner rendered and the decision cited must be the same decision.

    The payload is ordered by ``winning_root_id`` and the manifest cites
    ``selection_id``. If those ever describe different rows, a reader sees one
    event leading while the provenance names another — and the review status is
    derived from the row that was *not* rendered.
    """
    for events in (
        [_make_event(session, key="alpha"), _make_event(session, key="beta")],
        [
            _make_event(session, key="gamma"),
            _make_event(session, key="delta"),
            _make_event(session, key="epsilon"),
        ],
    ):
        evaluation = _apply(session, events)
        assert evaluation is not None
        bound = session.get(EditorialSelection, evaluation.selection_id)
        assert bound is not None
        assert bound.resolved_claim_id == evaluation.winning_root_id
        assert bound.decision_version == evaluation.selection_version
        assert (
            bound.reviewed_by == STANDING_FEATURED_EVENT_RULE
        ) == (evaluation.selection_origin == FEATURED_ORIGIN_STANDING_RULE)


@pytest.mark.integration
def test_a_human_choice_landing_first_is_honoured_not_overwritten(
    session: Session, tmp_path: Path
) -> None:
    """The concurrent case, made deterministic.

    Under a race a human choice can be recorded between the rule reading the
    current selections and writing its own. The writer refuses to displace it
    and returns that person's row; the evaluation must then describe *that*
    decision rather than the hash winner it set out to record.
    """
    first = _make_event(session, key="alpha")
    second = _make_event(session, key="beta")
    from app.governance import record_featured_event_selection

    # The human decision exists before the rule evaluates, which is the state a
    # race leaves behind by the time the writer takes the lock.
    human = record_featured_event_selection(
        session,
        profile_date=PROFILE_DATE,
        candidate_root_ids=[first.resolved_claim_id, second.resolved_claim_id],
        chosen_root_id=second.resolved_claim_id,
        reviewer=HUMAN,
        rationale="A person chose while the rule was mid-evaluation.",
    )

    evaluation = _apply(session, [first, second])

    assert evaluation is not None
    assert evaluation.selection_id == human.id
    assert evaluation.winning_root_id == second.resolved_claim_id
    assert evaluation.selection_origin == FEATURED_ORIGIN_HUMAN
    assert evaluation.decision_changed is False
