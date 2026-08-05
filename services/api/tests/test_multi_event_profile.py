"""A date that admits two distinct events must publish both (Golden 100 / G3b-1).

G3a made a human ``distinct_event`` decision durable and let the second event
past the collision guard. It did not change what publication *writes*: the
publisher still rebuilds ``recorded_on_this_date`` from one event's predicates
and carries every other section forward, so publishing B after
``distinct_event(A, B)`` silently drops A from the date it still occurred on.

Featured means emphasized first, not retained alone.

The display consequence is the obvious one. The dangerous consequence is
collision safety: ``events_behind_manifest`` resolves the admitted event set
from what the manifest publishes, so an A that vanishes from the manifest takes
its identity with it. A later candidate C carrying only ``distinct_event(C, A)``
would then bypass the guard without anyone having judged C against B -- the
system forgetting an identity decision it had already recorded.

These tests pin the invariant before the standing rule exists: preserve reality
first, choose the headline second.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.coverage import rebuild_coverage_index
from app.governance import (
    FEATURED_EVENT_SECTION,
    EditorialSelectionStatus,
    IdentityAdjudicationDecision,
    adjudicated_distinct,
    events_behind_manifest,
    record_editorial_selection,
    record_featured_event_selection,
)
from app.models import (
    Event,
    EventTime,
    ProfileType,
    PublicationManifest,
    PublicationRecordedEvent,
    PublicationStatementEvidence,
)
from app.services import (
    LocalFilesystemPublishedProfileStore,
    PublicationStatementEvidenceInput,
    RecordedEventBinding,
    publish_day_profile,
)
from app.wikidata import (
    _retained_recorded_groups,
    publish_wikidata_event,
    resolve_merge_review,
)

from .test_identity_adjudication import _make_event
from .test_usgs_vertical_slice import publish as publish_golden
from .test_wikidata_publish import (
    GOLDEN_DATE,
    _prepare_for_publication,
    _wikidata_event,
)

HUMAN = "test-human"


def _usgs_event(session: Session, *, other_than: Event) -> Event:
    """The golden date's already-published recorded event."""
    event = session.scalars(
        select(Event).where(Event.id != other_than.id)
    ).first()
    assert event is not None
    return event


def _feature(session: Session, *, chosen: Event, candidates: list[Event]) -> None:
    """A human features one of the date's events among the eligible set."""
    record_featured_event_selection(
        session,
        profile_date=GOLDEN_DATE,
        candidate_root_ids=[event.resolved_claim_id for event in candidates],
        chosen_root_id=chosen.resolved_claim_id,
        reviewer=HUMAN,
        rationale="A human featured this event for the date.",
    )


def _admit_both(
    session: Session, tmp_path: Path
) -> tuple[LocalFilesystemPublishedProfileStore, Event, Event]:
    """A published USGS event, and a Wikidata event a human ruled distinct from it."""
    publish_golden(session, tmp_path)
    _prepare_for_publication(session, tmp_path)
    rebuild_coverage_index(session)
    session.flush()
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")

    deferred = publish_wikidata_event(session, store=store)
    assert deferred.status == "deferred_to_merge_review"
    resolve_merge_review(
        session,
        decision=IdentityAdjudicationDecision.DISTINCT_EVENT,
        reviewer=HUMAN,
        rationale="Two different events that share 1964-03-27.",
    )
    session.flush()

    wikidata = _wikidata_event(session)
    usgs = _usgs_event(session, other_than=wikidata)
    assert adjudicated_distinct(
        session, event_a_id=wikidata.id, event_b_id=usgs.id
    )
    return store, usgs, wikidata


@pytest.mark.integration
def test_publishing_the_second_event_keeps_the_first(
    session: Session, tmp_path: Path
) -> None:
    """The defect, stated as a requirement.

    A human said these are two different events that happened on the same day.
    Publishing the second must not delete the first from the day it happened.
    """
    store, usgs, wikidata = _admit_both(session, tmp_path)
    _feature(session, chosen=wikidata, candidates=[usgs, wikidata])

    outcome = publish_wikidata_event(session, store=store)

    assert outcome.status == "published"
    assert outcome.manifest_id is not None
    manifest = session.get(PublicationManifest, outcome.manifest_id)
    assert manifest is not None
    assert events_behind_manifest(session, manifest=manifest) == {
        usgs.id,
        wikidata.id,
    }


@pytest.mark.integration
def test_the_published_payload_carries_both_events(
    session: Session, tmp_path: Path
) -> None:
    """Both events' statements are readable in the artifact a reader is served."""
    store, usgs, wikidata = _admit_both(session, tmp_path)
    _feature(session, chosen=wikidata, candidates=[usgs, wikidata])

    outcome = publish_wikidata_event(session, store=store)

    manifest = session.get(PublicationManifest, outcome.manifest_id)
    assert manifest is not None
    payload = store.read(manifest.storage_uri, manifest.content_hash)
    recorded = payload["sections"]["recorded_on_this_date"]
    statements = " ".join(str(entry.get("statement", "")) for entry in recorded)
    assert "USGS" in statements, "the previously published event was dropped"
    assert "Wikidata" in statements, "the newly published event is missing"


@pytest.mark.integration
def test_a_non_featured_event_still_blocks_an_unadjudicated_candidate(
    session: Session, tmp_path: Path
) -> None:
    """The collision-safety reason this matters, not the display reason.

    Once A and B are published together, a third candidate has to be judged
    against both. If the non-featured event drops out of the manifest, a
    decision about A alone would quietly authorise publication past B.
    """
    store, usgs, wikidata = _admit_both(session, tmp_path)
    _feature(session, chosen=wikidata, candidates=[usgs, wikidata])
    outcome = publish_wikidata_event(session, store=store)
    rebuild_coverage_index(session)
    session.flush()

    manifest = session.get(PublicationManifest, outcome.manifest_id)
    assert manifest is not None
    admitted = events_behind_manifest(session, manifest=manifest)

    # A candidate judged against only one of the two is not cleared for the date.
    assert usgs.id in admitted
    assert wikidata.id in admitted
    assert len(admitted) == 2


@pytest.mark.integration
def test_a_feature_switch_removes_neither_event(
    session: Session, tmp_path: Path
) -> None:
    """Switching the headline is a new version of the same admitted set."""
    store, usgs, wikidata = _admit_both(session, tmp_path)
    _feature(session, chosen=wikidata, candidates=[usgs, wikidata])
    first = publish_wikidata_event(session, store=store)
    rebuild_coverage_index(session)
    session.flush()

    _feature(session, chosen=usgs, candidates=[usgs, wikidata])
    second = publish_wikidata_event(session, store=store, force_new_version=True)

    assert second.manifest_id is not None
    assert second.manifest_id != first.manifest_id
    manifest = session.get(PublicationManifest, second.manifest_id)
    assert manifest is not None
    assert events_behind_manifest(session, manifest=manifest) == {
        usgs.id,
        wikidata.id,
    }
    # The prior version is untouched history, still carrying both events.
    previous = session.get(PublicationManifest, first.manifest_id)
    assert previous is not None
    assert events_behind_manifest(session, manifest=previous) == {
        usgs.id,
        wikidata.id,
    }


@pytest.mark.integration
def test_a_multi_event_date_fails_closed_without_a_featured_selection(
    session: Session, tmp_path: Path
) -> None:
    """No headline, no publication.

    The deterministic default is G3b-2's job. Until it exists, a date that
    admits two events and has no human choice must refuse rather than pick one
    by query order.
    """
    store, _usgs, _wikidata = _admit_both(session, tmp_path)

    with pytest.raises(Exception, match="featured"):
        publish_wikidata_event(session, store=store)


@pytest.mark.integration
def test_a_withdrawn_predicate_is_not_preserved_by_republication(
    session: Session, tmp_path: Path
) -> None:
    """Recorded material is rebuilt from current selections, never copied.

    Carrying the prior artifact's recorded statements forward would let a
    predicate a human has since rejected survive in the new version, which is
    the profile asserting something nobody currently stands behind.
    """
    store, usgs, wikidata = _admit_both(session, tmp_path)
    _feature(session, chosen=wikidata, candidates=[usgs, wikidata])
    publish_wikidata_event(session, store=store)
    rebuild_coverage_index(session)
    session.flush()

    # A human withdraws one of the Wikidata predicates after the first publish.
    from .test_wikidata_publish import _resolved

    coordinates = _resolved(session, "candidate_coordinates")
    assert coordinates is not None
    record_editorial_selection(
        session,
        profile_date=GOLDEN_DATE,
        section_key="recorded_on_this_date",
        resolved_claim_id=coordinates.id,
        status=EditorialSelectionStatus.REJECTED,
        display_rank=None,
        rationale="Withdrawn after the first publication.",
        reviewed_by=HUMAN,
    )
    session.flush()

    outcome = publish_wikidata_event(session, store=store, force_new_version=True)

    manifest = session.get(PublicationManifest, outcome.manifest_id)
    assert manifest is not None
    payload = store.read(manifest.storage_uri, manifest.content_hash)
    recorded = payload["sections"]["recorded_on_this_date"]
    statements = " ".join(str(entry.get("statement", "")) for entry in recorded)
    # Assert on the root, not on a word: "latitude" also appears in the USGS
    # coordinates statement, which is legitimately retained, so a substring
    # check here would pass or fail for the wrong reason.
    evidence_roots = set(
        session.scalars(
            select(PublicationStatementEvidence.resolved_claim_id).where(
                PublicationStatementEvidence.publication_manifest_id == manifest.id
            )
        )
    )
    assert coordinates.id not in evidence_roots, (
        "a withdrawn predicate was carried forward"
    )
    assert "Wikidata places the event at" not in statements
    # The other event's coordinates are untouched by a decision about this one.
    assert "USGS places the epicenter at" in statements
    # Both events are still present; only the withdrawn predicate is gone.
    assert events_behind_manifest(session, manifest=manifest) == {
        usgs.id,
        wikidata.id,
    }


@pytest.mark.integration
def test_a_single_event_date_writes_no_feature_governance_row(
    session: Session, tmp_path: Path
) -> None:
    """One event is not a choice, and must not manufacture an editorial decision."""
    from app.governance import EditorialSelection

    _prepare_for_publication(session, tmp_path)
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")

    outcome = publish_wikidata_event(session, store=store)

    assert outcome.status == "published"
    assert (
        session.scalars(
            select(EditorialSelection).where(
                EditorialSelection.section_key == FEATURED_EVENT_SECTION
            )
        ).first()
        is None
    )


@pytest.mark.integration
def test_the_admitted_set_commits_with_the_manifest(
    session: Session, tmp_path: Path
) -> None:
    """The binding is part of publication, not a follow-up write.

    ``publish_day_profile`` commits and indexes coverage before returning, so the
    manifest is discoverable the moment it comes back. A binding written after
    that leaves a window where the date exists with its admitted event set
    missing -- and ``events_behind_manifest`` falls back to the inference this
    table was added to replace, silently under-reporting the very set the
    collision guard checks against.

    Read on a separate connection, which sees only committed state.
    """
    store, usgs, wikidata = _admit_both(session, tmp_path)
    _feature(session, chosen=wikidata, candidates=[usgs, wikidata])

    outcome = publish_wikidata_event(session, store=store)

    engine = session.get_bind()
    assert isinstance(engine, Engine)
    with engine.connect() as connection:
        bound = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT event_id FROM publication_recorded_events "
                    "WHERE publication_manifest_id = :manifest"
                ),
                {"manifest": outcome.manifest_id},
            )
        }
    assert bound == {usgs.id, wikidata.id}


@pytest.mark.integration
def test_retained_statements_are_grouped_per_event_so_the_headline_can_lead(
    session: Session, tmp_path: Path
) -> None:
    """The page must not lead with one event while the binding names another.

    Carried forward as one flat block, two retained events keep whichever came
    first in the previous version at the top regardless of the headline, so the
    reader sees a lead that contradicts the manifest. Grouping is what lets the
    featured event move to the front.
    """
    first = _make_event(session, key="first-retained")
    second = _make_event(session, key="second-retained")
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    prior = _publish_two_event_prior(session, store, first=first, second=second)

    groups = _retained_recorded_groups(
        session,
        store=store,
        manifest=prior,
        profile_date=GOLDEN_DATE,
        rebuilt_key_prefix="wikidata:nothing:",
    )

    # Grouped per event, in the order the prior version recorded them.
    assert [group[0] for group in groups] == [first.id, second.id]
    assert all(len(group[1]) == 1 for group in groups)
    # And the headline can therefore lead, whichever event it is.
    ordered = sorted(groups, key=lambda group: group[0] != second.id)
    assert ordered[0][0] == second.id


def _publish_two_event_prior(
    session: Session,
    store: LocalFilesystemPublishedProfileStore,
    *,
    first: Event,
    second: Event,
) -> PublicationManifest:
    """A prior version that already admitted two events, in a known order."""
    roots: list[uuid.UUID] = []
    for event in (first, second):
        occurrence = session.scalar(
            select(EventTime.provenance_resolved_claim_id).where(
                EventTime.event_id == event.id, EventTime.is_primary.is_(True)
            )
        )
        assert occurrence is not None
        roots.append(occurrence)
        record_editorial_selection(
            session,
            profile_date=GOLDEN_DATE,
            section_key="recorded_on_this_date",
            resolved_claim_id=occurrence,
            status=EditorialSelectionStatus.SELECTED,
            display_rank=None,
            rationale="Selected for the prior version.",
            reviewed_by=HUMAN,
        )
    session.flush()
    profile = publish_day_profile(
        session,
        store=store,
        profile_date=GOLDEN_DATE,
        profile_type=ProfileType.STANDARD_STATISTICAL,
        payload={
            "schema_version": "1",
            "date": GOLDEN_DATE.isoformat(),
            "profile_type": ProfileType.STANDARD_STATISTICAL.value,
            "sections": {
                "recorded_on_this_date": [
                    {
                        "statement_id": f"prior-{index}",
                        "statement": f"Prior recorded event {index}.",
                        "details": {},
                        "provenance_note": "development fixture",
                    }
                    for index in range(2)
                ]
            },
            "section_states": {"recorded_on_this_date": {"status": "available"}},
        },
        statement_evidence=[
            PublicationStatementEvidenceInput(
                statement_path=f"/sections/recorded_on_this_date/{index}",
                resolved_claim_id=root,
            )
            for index, root in enumerate(roots)
        ],
        recorded_events=[
            RecordedEventBinding(
                event_id=first.id,
                is_featured=True,
                featured_selection_id=None,
                statement_count=1,
            ),
            RecordedEventBinding(
                event_id=second.id,
                is_featured=False,
                featured_selection_id=None,
                statement_count=1,
            ),
        ],
    )
    manifest = session.get(PublicationManifest, profile.publication_manifest_id)
    assert manifest is not None
    return manifest


@pytest.mark.integration
def test_a_successor_may_not_drop_a_previously_admitted_event(
    session: Session, tmp_path: Path
) -> None:
    """Forgetting an admitted event fails loudly rather than silently.

    A publisher that cannot yet carry the other events on its date is
    recoverable; a successor that quietly forgets one of them is not, because
    the collision guard simply stops seeing it.
    """
    first = _make_event(session, key="first-admitted")
    second = _make_event(session, key="second-admitted")
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    _publish_two_event_prior(session, store, first=first, second=second)

    only_first = session.scalar(
        select(EventTime.provenance_resolved_claim_id).where(
            EventTime.event_id == first.id, EventTime.is_primary.is_(True)
        )
    )
    assert only_first is not None

    with pytest.raises(ValueError, match="would drop"):
        publish_day_profile(
            session,
            store=store,
            profile_date=GOLDEN_DATE,
            profile_type=ProfileType.STANDARD_STATISTICAL,
            payload={
                "schema_version": "1",
                "date": GOLDEN_DATE.isoformat(),
                "profile_type": ProfileType.STANDARD_STATISTICAL.value,
                "sections": {
                    "recorded_on_this_date": [
                        {
                            "statement_id": "only-first",
                            "statement": "A successor carrying one event only.",
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
                    resolved_claim_id=only_first,
                )
            ],
            recorded_events=[
                RecordedEventBinding(
                    event_id=first.id,
                    is_featured=True,
                    featured_selection_id=None,
                    statement_count=1,
                )
            ],
            force_new_version=True,
        )


@pytest.mark.integration
def test_the_golden_publisher_binds_its_recorded_event(
    session: Session, tmp_path: Path
) -> None:
    """The other real recorded-event publisher declares its admitted event too.

    Fixing only the publisher in front of me would leave the next USGS run able
    to mint a successor with no bound rows, so the admitted set would fall back
    to inference and a co-published event would drop out of the guard again.
    """
    _, golden = publish_golden(session, tmp_path)

    manifest = session.get(PublicationManifest, golden.publication_manifest_id)
    assert manifest is not None
    bound = events_behind_manifest(session, manifest=manifest)
    assert len(bound) == 1
    rows = list(
        session.scalars(
            select(PublicationRecordedEvent).where(
                PublicationRecordedEvent.publication_manifest_id == manifest.id
            )
        )
    )
    assert [row.is_featured for row in rows] == [True]
    assert rows[0].statement_count > 0


@pytest.mark.integration
def test_an_empty_recorded_section_may_not_erase_an_admitted_event(
    session: Session, tmp_path: Path
) -> None:
    """Dropping every event is still dropping.

    The exemption for empty recorded sections exists so context-only profiles
    keep publishing. Applied before checking what the date already admits, it
    becomes the widest possible drop: a successor with no recorded section and
    no bindings leaves ``events_behind_manifest`` empty, and the collision guard
    forgets the date entirely.
    """
    first = _make_event(session, key="admitted-before-erasure")
    second = _make_event(session, key="also-admitted")
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    _publish_two_event_prior(session, store, first=first, second=second)

    with pytest.raises(ValueError, match="would drop"):
        publish_day_profile(
            session,
            store=store,
            profile_date=GOLDEN_DATE,
            profile_type=ProfileType.STANDARD_STATISTICAL,
            payload={
                "schema_version": "1",
                "date": GOLDEN_DATE.isoformat(),
                "profile_type": ProfileType.STANDARD_STATISTICAL.value,
                "sections": {
                    "recorded_on_this_date": [],
                    "typical_day_in_this_year": [
                        {
                            "statement_id": "context",
                            "statement": "Context that would bury the events.",
                            "details": {},
                            "provenance_note": "development fixture",
                        }
                    ],
                },
                "section_states": {
                    "typical_day_in_this_year": {"status": "available"}
                },
            },
            statement_evidence=[
                PublicationStatementEvidenceInput(
                    statement_path="/sections/typical_day_in_this_year/0",
                    resolved_claim_id=first.resolved_claim_id,
                )
            ],
            force_new_version=True,
        )


@pytest.mark.integration
def test_an_empty_recorded_section_is_fine_where_nothing_was_admitted(
    session: Session, tmp_path: Path
) -> None:
    """The other side: context-only dates must keep publishing.

    Most of the archive is context. A rule that refused every empty recorded
    section would stop the archive republishing rather than protect anything.
    """
    event = _make_event(session, key="context-only-date")
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")

    profile = publish_day_profile(
        session,
        store=store,
        profile_date=GOLDEN_DATE,
        profile_type=ProfileType.STANDARD_STATISTICAL,
        payload={
            "schema_version": "1",
            "date": GOLDEN_DATE.isoformat(),
            "profile_type": ProfileType.STANDARD_STATISTICAL.value,
            "sections": {
                "recorded_on_this_date": [],
                "typical_day_in_this_year": [
                    {
                        "statement_id": "context",
                        "statement": "Annual context only.",
                        "details": {},
                        "provenance_note": "development fixture",
                    }
                ],
            },
            "section_states": {"typical_day_in_this_year": {"status": "available"}},
        },
        statement_evidence=[
            PublicationStatementEvidenceInput(
                statement_path="/sections/typical_day_in_this_year/0",
                resolved_claim_id=event.resolved_claim_id,
            )
        ],
    )

    assert profile.publication_manifest_id is not None
