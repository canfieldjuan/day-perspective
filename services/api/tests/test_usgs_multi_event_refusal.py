"""The golden publisher refuses a date it can no longer fully represent (#86).

G3b-1 (#83) made a date able to publish several canonical events once a human
has adjudicated them distinct. The golden USGS publisher was not part of that
change: it rebuilds ``recorded_on_this_date`` from its own statements alone.

On a single-event date that is exactly right. On a date that has since admitted
another publisher's event it is amnesia — the successor carries only USGS
evidence, ``events_behind_manifest`` infers only the USGS event, and the
collision guard stops believing the co-published event was ever there. A later
candidate is then checked against USGS alone, and a ``distinct_event`` decision a
human actually made never runs.

The boundary is deliberate. The USGS publisher knows how to render USGS
statements; it should not become responsible for carrying and revalidating
another source's content merely because both events happened on the same day.
So it refuses, by name, and says what would be needed instead.

Safe inability is better than successful amnesia.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.coverage import rebuild_coverage_index
from app.governance import events_behind_manifest
from app.models import DayProfile, PublicationManifest
from app.services import LocalFilesystemPublishedProfileStore
from app.usgs import MultiEventDateRequiresCombinedPublisher, publish_golden_profile
from app.wikidata import publish_wikidata_event

from .test_usgs_vertical_slice import publish as publish_golden
from .test_wikidata_publish import (
    _feature,
    _golden_event,
    _publish_past_the_golden_collision,
    _wikidata_event,
)


def _publish_both_events(
    session: Session, tmp_path: Path
) -> tuple[LocalFilesystemPublishedProfileStore, PublicationManifest]:
    """A date genuinely publishing two adjudicated-distinct events."""
    store, golden = _publish_past_the_golden_collision(session, tmp_path)
    golden_event = _golden_event(session, golden)
    wikidata_event = _wikidata_event(session)
    _feature(session, candidates=[golden_event, wikidata_event], chosen=wikidata_event)

    outcome = publish_wikidata_event(session, store=store)
    assert outcome.status == "published"
    rebuild_coverage_index(session)
    session.flush()

    manifest = session.get(PublicationManifest, outcome.manifest_id)
    assert manifest is not None
    assert events_behind_manifest(session, manifest=manifest) == {
        golden_event.id,
        wikidata_event.id,
    }
    return store, manifest


@pytest.mark.integration
def test_a_golden_rerun_on_a_multi_event_date_is_refused_by_name(
    session: Session, tmp_path: Path
) -> None:
    """The refusal itself, and that it says which events it cannot carry."""
    store, _manifest = _publish_both_events(session, tmp_path)

    with pytest.raises(MultiEventDateRequiresCombinedPublisher) as raised:
        publish_golden_profile(session, store=store, force_new_version=True)

    assert (
        raised.value.code == "multi_event_date_requires_combined_publisher"
    ), "the refusal carries a stable code, not just prose"
    assert str(_wikidata_event(session).id) in str(raised.value), (
        "the refusal names the event it cannot preserve"
    )


@pytest.mark.integration
def test_the_refused_rerun_mints_nothing(
    session: Session, tmp_path: Path
) -> None:
    """A refusal that still published something would be the worse outcome."""
    store, manifest = _publish_both_events(session, tmp_path)
    manifests_before = session.scalar(
        select(func.count()).select_from(PublicationManifest)
    )
    profiles_before = session.scalar(select(func.count()).select_from(DayProfile))
    hash_before = manifest.content_hash

    with pytest.raises(MultiEventDateRequiresCombinedPublisher):
        publish_golden_profile(session, store=store, force_new_version=True)

    assert (
        session.scalar(select(func.count()).select_from(PublicationManifest))
        == manifests_before
    )
    assert (
        session.scalar(select(func.count()).select_from(DayProfile))
        == profiles_before
    )
    session.refresh(manifest)
    assert manifest.content_hash == hash_before


@pytest.mark.integration
def test_the_date_still_resolves_both_events_after_the_refusal(
    session: Session, tmp_path: Path
) -> None:
    """The point of refusing: the archive still knows what happened that day."""
    store, manifest = _publish_both_events(session, tmp_path)
    expected = events_behind_manifest(session, manifest=manifest)
    assert len(expected) == 2

    with pytest.raises(MultiEventDateRequiresCombinedPublisher):
        publish_golden_profile(session, store=store, force_new_version=True)

    current = session.scalar(
        select(PublicationManifest)
        .where(PublicationManifest.profile_date == manifest.profile_date)
        .order_by(PublicationManifest.version.desc())
    )
    assert current is not None
    assert current.id == manifest.id
    assert events_behind_manifest(session, manifest=current) == expected


@pytest.mark.integration
def test_an_ordinary_single_event_golden_rerun_still_succeeds(
    session: Session, tmp_path: Path
) -> None:
    """The other side. Most dates are single-event and must be unaffected.

    A refusal that also blocked the ordinary rerun would trade one outage for a
    wider one, and would look identical in a passing test suite that only
    exercised the failure.
    """
    publish_golden(session, tmp_path)
    rebuild_coverage_index(session)
    session.flush()
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")

    profile = publish_golden_profile(session, store=store, force_new_version=True)

    assert profile.publication_manifest_id is not None
    manifest = session.get(PublicationManifest, profile.publication_manifest_id)
    assert manifest is not None
    assert len(events_behind_manifest(session, manifest=manifest)) == 1


@pytest.mark.integration
def test_the_first_golden_publication_is_unaffected(
    session: Session, tmp_path: Path
) -> None:
    """A date with no prior manifest has nothing to forget."""
    _, golden = publish_golden(session, tmp_path)

    manifest = session.get(PublicationManifest, golden.publication_manifest_id)
    assert manifest is not None
    assert len(events_behind_manifest(session, manifest=manifest)) == 1
