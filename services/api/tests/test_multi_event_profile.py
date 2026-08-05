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

from pathlib import Path

import pytest
from sqlalchemy import select
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
from app.models import Event, PublicationManifest, PublicationStatementEvidence
from app.services import LocalFilesystemPublishedProfileStore
from app.wikidata import publish_wikidata_event, resolve_merge_review

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
