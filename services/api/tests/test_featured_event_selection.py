"""Featuring one of a date's events is a single choice, not several yes/no ones.

`record_editorial_selection` versions decisions per `(date, section, root)`. That
is right for "should this statement be published", where each root is an
independent question — and wrong for "which of these events is the headline",
where choosing B must un-choose A. A human `SELECTED` on event B's identity root
does not supersede a standing rule's `SELECTED` on event A's: both stay selected
on independent counters, and comparing `decision_version` between roots is
meaningless because the counters never shared a scale.

So the writer here takes the *complete* eligible candidate set, under one
advisory lock on the date, and leaves exactly one root selected. The resolver
fails closed on zero or several rather than let query order pick the headline.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.governance import (
    FEATURED_EVENT_SECTION,
    STANDING_FEATURED_EVENT_RULE,
    EditorialSelection,
    EditorialSelectionStatus,
    FeaturedEventUnresolved,
    record_editorial_selection,
    record_featured_event_selection,
    resolve_featured_event,
)
from app.models import Event

from .test_identity_adjudication import HUMAN, OTHER_DATE, PROFILE_DATE, _make_event


def _identity_root(event: Event) -> uuid.UUID:
    return event.resolved_claim_id


def _latest_featured(
    session: Session, root_id: uuid.UUID
) -> EditorialSelection | None:
    return session.scalars(
        select(EditorialSelection)
        .where(
            EditorialSelection.profile_date == PROFILE_DATE,
            EditorialSelection.section_key == FEATURED_EVENT_SECTION,
            EditorialSelection.resolved_claim_id == root_id,
        )
        .order_by(EditorialSelection.decision_version.desc())
    ).first()


def _featured_rows(session: Session) -> int | None:
    return session.scalar(
        select(func.count())
        .select_from(EditorialSelection)
        .where(EditorialSelection.section_key == FEATURED_EVENT_SECTION)
    )


@pytest.mark.integration
def test_featuring_one_event_rejects_every_other_candidate(
    session: Session,
) -> None:
    a = _make_event(session, key="A")
    b = _make_event(session, key="B")
    c = _make_event(session, key="C")
    roots = [_identity_root(event) for event in (a, b, c)]

    record_featured_event_selection(
        session,
        profile_date=PROFILE_DATE,
        candidate_root_ids=roots,
        chosen_root_id=roots[1],
        reviewer=HUMAN,
        rationale="A human featured event B for this date.",
    )

    chosen = _latest_featured(session, roots[1])
    assert chosen is not None
    assert chosen.status == EditorialSelectionStatus.SELECTED.value
    for other in (roots[0], roots[2]):
        row = _latest_featured(session, other)
        assert row is not None
        assert row.status == EditorialSelectionStatus.REJECTED.value
    assert (
        resolve_featured_event(
            session, profile_date=PROFILE_DATE, candidate_root_ids=roots
        )
        == roots[1]
    )


@pytest.mark.integration
def test_repeating_the_same_featured_choice_is_idempotent(
    session: Session,
) -> None:
    a = _make_event(session, key="A")
    b = _make_event(session, key="B")
    roots = [_identity_root(event) for event in (a, b)]

    def _feature() -> None:
        record_featured_event_selection(
            session,
            profile_date=PROFILE_DATE,
            candidate_root_ids=roots,
            chosen_root_id=roots[0],
            reviewer=HUMAN,
            rationale="A human featured event A for this date.",
        )

    _feature()
    before = _featured_rows(session)
    _feature()

    assert _featured_rows(session) == before


@pytest.mark.integration
def test_a_selected_root_dropped_from_the_candidate_set_is_not_left_selected(
    session: Session,
) -> None:
    """A stale winner must not survive a candidate set it is no longer in."""
    a = _make_event(session, key="A")
    b = _make_event(session, key="B")
    c = _make_event(session, key="C")
    root_a, root_b, root_c = (_identity_root(event) for event in (a, b, c))

    record_featured_event_selection(
        session,
        profile_date=PROFILE_DATE,
        candidate_root_ids=[root_a, root_b],
        chosen_root_id=root_a,
        reviewer=HUMAN,
        rationale="A human featured event A.",
    )
    # A later pass sees a different eligible set that no longer contains A.
    record_featured_event_selection(
        session,
        profile_date=PROFILE_DATE,
        candidate_root_ids=[root_b, root_c],
        chosen_root_id=root_c,
        reviewer=HUMAN,
        rationale="A human featured event C after A was withdrawn.",
    )

    stale = _latest_featured(session, root_a)
    assert stale is not None
    assert stale.status == EditorialSelectionStatus.REJECTED.value
    assert (
        resolve_featured_event(
            session,
            profile_date=PROFILE_DATE,
            candidate_root_ids=[root_b, root_c],
        )
        == root_c
    )


@pytest.mark.integration
def test_a_standing_rule_cannot_displace_a_human_featured_choice(
    session: Session,
) -> None:
    """D038: a pass never overrules a human."""
    a = _make_event(session, key="A")
    b = _make_event(session, key="B")
    roots = [_identity_root(event) for event in (a, b)]
    record_featured_event_selection(
        session,
        profile_date=PROFILE_DATE,
        candidate_root_ids=roots,
        chosen_root_id=roots[0],
        reviewer=HUMAN,
        rationale="A human featured event A.",
    )

    record_featured_event_selection(
        session,
        profile_date=PROFILE_DATE,
        candidate_root_ids=roots,
        chosen_root_id=roots[1],
        reviewer=STANDING_FEATURED_EVENT_RULE,
        rationale="Deterministic default.",
    )

    assert (
        resolve_featured_event(
            session, profile_date=PROFILE_DATE, candidate_root_ids=roots
        )
        == roots[0]
    )


@pytest.mark.integration
def test_a_standing_rule_may_choose_where_no_human_has(session: Session) -> None:
    """The other side of D038: the rule fills a gap, it just never overrules."""
    a = _make_event(session, key="A")
    b = _make_event(session, key="B")
    roots = [_identity_root(event) for event in (a, b)]

    record_featured_event_selection(
        session,
        profile_date=PROFILE_DATE,
        candidate_root_ids=roots,
        chosen_root_id=roots[1],
        reviewer=STANDING_FEATURED_EVENT_RULE,
        rationale="Deterministic default with no human decision on record.",
    )

    assert (
        resolve_featured_event(
            session, profile_date=PROFILE_DATE, candidate_root_ids=roots
        )
        == roots[1]
    )


@pytest.mark.integration
def test_a_human_may_override_a_standing_rule_choice(session: Session) -> None:
    a = _make_event(session, key="A")
    b = _make_event(session, key="B")
    roots = [_identity_root(event) for event in (a, b)]
    record_featured_event_selection(
        session,
        profile_date=PROFILE_DATE,
        candidate_root_ids=roots,
        chosen_root_id=roots[1],
        reviewer=STANDING_FEATURED_EVENT_RULE,
        rationale="Deterministic default.",
    )

    record_featured_event_selection(
        session,
        profile_date=PROFILE_DATE,
        candidate_root_ids=roots,
        chosen_root_id=roots[0],
        reviewer=HUMAN,
        rationale="A person read the date and chose A.",
    )

    assert (
        resolve_featured_event(
            session, profile_date=PROFILE_DATE, candidate_root_ids=roots
        )
        == roots[0]
    )


@pytest.mark.integration
def test_the_chosen_root_must_be_one_of_the_candidates(session: Session) -> None:
    a = _make_event(session, key="A")
    b = _make_event(session, key="B")
    outsider = _make_event(session, key="C")

    with pytest.raises(FeaturedEventUnresolved):
        record_featured_event_selection(
            session,
            profile_date=PROFILE_DATE,
            candidate_root_ids=[_identity_root(a), _identity_root(b)],
            chosen_root_id=_identity_root(outsider),
            reviewer=HUMAN,
            rationale="Chosen root is not eligible.",
        )


@pytest.mark.integration
def test_candidates_must_be_events_that_occur_on_the_date(
    session: Session,
) -> None:
    a = _make_event(session, key="A")
    elsewhere = _make_event(session, key="C", on_date=OTHER_DATE)

    with pytest.raises(FeaturedEventUnresolved):
        record_featured_event_selection(
            session,
            profile_date=PROFILE_DATE,
            candidate_root_ids=[_identity_root(a), _identity_root(elsewhere)],
            chosen_root_id=_identity_root(a),
            reviewer=HUMAN,
            rationale="A candidate that does not occur on this date.",
        )


@pytest.mark.integration
def test_a_root_that_is_not_an_event_identity_is_rejected(
    session: Session,
) -> None:
    a = _make_event(session, key="A")

    with pytest.raises(FeaturedEventUnresolved):
        record_featured_event_selection(
            session,
            profile_date=PROFILE_DATE,
            candidate_root_ids=[_identity_root(a), uuid.uuid4()],
            chosen_root_id=_identity_root(a),
            reviewer=HUMAN,
            rationale="A candidate that is not an event identity root.",
        )


@pytest.mark.integration
def test_resolver_returns_nothing_when_no_event_is_eligible(
    session: Session,
) -> None:
    assert (
        resolve_featured_event(
            session, profile_date=PROFILE_DATE, candidate_root_ids=[]
        )
        is None
    )


@pytest.mark.integration
def test_a_single_eligible_event_needs_no_governance_row(session: Session) -> None:
    """One event is not a choice, and must not manufacture an editorial decision."""
    a = _make_event(session, key="A")
    root = _identity_root(a)

    assert (
        resolve_featured_event(
            session, profile_date=PROFILE_DATE, candidate_root_ids=[root]
        )
        == root
    )
    assert _featured_rows(session) == 0


@pytest.mark.integration
def test_multiple_candidates_with_no_current_selection_fail_closed(
    session: Session,
) -> None:
    a = _make_event(session, key="A")
    b = _make_event(session, key="B")
    roots = [_identity_root(event) for event in (a, b)]

    with pytest.raises(FeaturedEventUnresolved):
        resolve_featured_event(
            session, profile_date=PROFILE_DATE, candidate_root_ids=roots
        )


@pytest.mark.integration
def test_two_independently_selected_roots_fail_closed(session: Session) -> None:
    """The exact defect the section-global writer exists to prevent.

    Two roots can each carry a latest ``SELECTED`` decision on independent
    per-root version counters. Reading that as "featured" would publish
    whichever the query happened to order first.
    """
    a = _make_event(session, key="A")
    b = _make_event(session, key="B")
    roots = [_identity_root(event) for event in (a, b)]
    for root in roots:
        record_editorial_selection(
            session,
            profile_date=PROFILE_DATE,
            section_key=FEATURED_EVENT_SECTION,
            resolved_claim_id=root,
            status=EditorialSelectionStatus.SELECTED,
            display_rank=None,
            rationale="Independently selected, as the per-root writer allows.",
            reviewed_by=HUMAN,
        )

    with pytest.raises(FeaturedEventUnresolved):
        resolve_featured_event(
            session, profile_date=PROFILE_DATE, candidate_root_ids=roots
        )


@pytest.mark.integration
def test_the_writer_leaves_exactly_one_selection_after_a_reshuffle(
    session: Session,
) -> None:
    """Whatever the sequence, the date ends with one headline, never two."""
    events = [_make_event(session, key=key) for key in ("A", "B", "C")]
    roots = [_identity_root(event) for event in events]

    for chosen in (roots[0], roots[2], roots[1]):
        record_featured_event_selection(
            session,
            profile_date=PROFILE_DATE,
            candidate_root_ids=roots,
            chosen_root_id=chosen,
            reviewer=HUMAN,
            rationale=f"A human featured {chosen}.",
        )
        selected = [
            root
            for root in roots
            if (row := _latest_featured(session, root)) is not None
            and row.status == EditorialSelectionStatus.SELECTED.value
        ]
        assert selected == [chosen]


@pytest.mark.integration
def test_a_date_with_no_selection_is_unaffected_by_another_dates_choice(
    session: Session,
) -> None:
    """The lock and the history are per date; one date's headline is not another's."""
    a = _make_event(session, key="A")
    b = _make_event(session, key="B")
    roots = [_identity_root(event) for event in (a, b)]
    record_featured_event_selection(
        session,
        profile_date=PROFILE_DATE,
        candidate_root_ids=roots,
        chosen_root_id=roots[0],
        reviewer=HUMAN,
        rationale="A human featured event A on this date.",
    )

    elsewhere = _make_event(session, key="D", on_date=OTHER_DATE)
    other = _make_event(session, key="E", on_date=OTHER_DATE)

    with pytest.raises(FeaturedEventUnresolved):
        resolve_featured_event(
            session,
            profile_date=OTHER_DATE,
            candidate_root_ids=[_identity_root(elsewhere), _identity_root(other)],
        )
