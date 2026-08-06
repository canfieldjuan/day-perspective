"""Recorded statements say which event they belong to (G3b-2b).

Until now the recorded section was a flat array whose only grouping signal was
order: the featured event's statements came first, and a reader — human or
renderer — had to infer where one event ended and the next began. G3b-1's
publisher comment says as much, deferring per-event grouping rather than
approximating it.

Position is not semantics. A renderer that groups by "everything until the next
one that looks different" is guessing, and the guess breaks the moment a date
holds three events or an event contributes one statement.

So each statement carries typed group metadata: which event it describes, that
event's title, whether it is the featured one, and both orderings. Grouping
becomes something the payload states rather than something a reader reconstructs.

The attribution rule is source-release lineage: a statement's evidence traces to
the release that produced it, and an event is resolved from a release too. That
is derivable for every publisher, unlike matching on identity or occurrence
provenance — USGS publishes title, magnitude and depth through neither.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.coverage import rebuild_coverage_index
from app.models import PublicationManifest
from app.services import LocalFilesystemPublishedProfileStore
from app.wikidata import publish_wikidata_event

from .test_wikidata_publish import (
    _golden_event,
    _prepare_for_publication,
    _publish_past_the_golden_collision,
    _wikidata_event,
)


def _recorded(
    session: Session,
    store: LocalFilesystemPublishedProfileStore,
    manifest_id: object,
) -> list[dict[str, Any]]:
    manifest = session.get(PublicationManifest, manifest_id)
    assert manifest is not None
    payload = store.read(manifest.storage_uri, manifest.content_hash)
    return list(payload["sections"]["recorded_on_this_date"])


@pytest.mark.integration
def test_every_recorded_statement_declares_its_event(
    session: Session, tmp_path: Path
) -> None:
    """Grouping is stated, not inferred from position."""
    store, golden = _publish_past_the_golden_collision(session, tmp_path)
    _golden_event(session, golden)
    _wikidata_event(session)

    outcome = publish_wikidata_event(session, store=store)
    assert outcome.status == "published"

    recorded = _recorded(session, store, outcome.manifest_id)
    assert recorded
    for statement in recorded:
        group = statement.get("event_group")
        assert group is not None, f"{statement['statement_id']} has no event group"
        assert group["event_group_key"]
        assert group["event_title"]
        assert isinstance(group["featured"], bool)
        assert isinstance(group["event_order"], int)
        assert isinstance(group["predicate_order"], int)


@pytest.mark.integration
def test_statements_of_one_event_share_a_group_key(
    session: Session, tmp_path: Path
) -> None:
    """Two events, two group keys, and no statement stranded between them."""
    store, _golden = _publish_past_the_golden_collision(session, tmp_path)

    outcome = publish_wikidata_event(session, store=store)

    recorded = _recorded(session, store, outcome.manifest_id)
    by_key: dict[str, set[str]] = defaultdict(set)
    for statement in recorded:
        by_key[statement["event_group"]["event_group_key"]].add(
            statement["statement_id"]
        )
    assert len(by_key) == 2, "a two-event date must publish exactly two groups"
    # The USGS and Wikidata statements land in different groups, not interleaved.
    for statement_ids in by_key.values():
        prefixes = {sid.startswith("wikidata-") for sid in statement_ids}
        assert len(prefixes) == 1, "a group mixes statements from two publishers"


@pytest.mark.integration
def test_exactly_one_group_is_featured_and_it_leads(
    session: Session, tmp_path: Path
) -> None:
    """One headline, and the payload orders it first."""
    store, _golden = _publish_past_the_golden_collision(session, tmp_path)

    outcome = publish_wikidata_event(session, store=store)

    recorded = _recorded(session, store, outcome.manifest_id)
    featured_keys = {
        statement["event_group"]["event_group_key"]
        for statement in recorded
        if statement["event_group"]["featured"]
    }
    assert len(featured_keys) == 1
    # event_order 0 is the featured group, and it appears first in the array.
    featured_key = next(iter(featured_keys))
    assert recorded[0]["event_group"]["event_group_key"] == featured_key
    assert recorded[0]["event_group"]["event_order"] == 0
    for statement in recorded:
        group = statement["event_group"]
        assert (group["event_order"] == 0) == group["featured"]


@pytest.mark.integration
def test_predicate_order_runs_within_each_group(
    session: Session, tmp_path: Path
) -> None:
    """Ordering inside a group is its own sequence, not a slice of the array.

    A renderer that grouped by array position would work by accident here; one
    that reads ``predicate_order`` keeps working when a group is rendered on its
    own, collapsed, or reordered.
    """
    store, _golden = _publish_past_the_golden_collision(session, tmp_path)

    outcome = publish_wikidata_event(session, store=store)

    recorded = _recorded(session, store, outcome.manifest_id)
    orders: dict[str, list[int]] = defaultdict(list)
    for statement in recorded:
        group = statement["event_group"]
        orders[group["event_group_key"]].append(group["predicate_order"])
    for key, sequence in orders.items():
        assert sequence == list(range(len(sequence))), (
            f"group {key} does not carry a 0-based contiguous predicate order"
        )


@pytest.mark.integration
def test_a_single_event_profile_still_declares_its_group(
    session: Session, tmp_path: Path
) -> None:
    """One event is still an event.

    A conditional shape would make every renderer branch on whether grouping is
    present. The metadata is always there; a single group is simply featured.
    """
    _prepare_for_publication(session, tmp_path)
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")

    outcome = publish_wikidata_event(session, store=store)
    assert outcome.status == "published"

    recorded = _recorded(session, store, outcome.manifest_id)
    keys = {statement["event_group"]["event_group_key"] for statement in recorded}
    assert len(keys) == 1
    assert all(statement["event_group"]["featured"] for statement in recorded)
    assert all(
        statement["event_group"]["event_order"] == 0 for statement in recorded
    )


@pytest.mark.integration
def test_a_multi_source_profile_does_not_claim_one_source(
    session: Session, tmp_path: Path
) -> None:
    """A USGS and Wikidata date is not a Wikidata page.

    The singular ``source_attribution`` names whichever publisher happened to
    write last. On a date whose recorded section rests on two sources, that is
    not a summary — it is wrong, and wrong in the direction that flatters the
    most recent contributor.
    """
    store, _golden = _publish_past_the_golden_collision(session, tmp_path)

    outcome = publish_wikidata_event(session, store=store)

    manifest = session.get(PublicationManifest, outcome.manifest_id)
    assert manifest is not None
    payload = store.read(manifest.storage_uri, manifest.content_hash)
    attributions = payload.get("source_attributions")
    assert attributions is not None, "a multi-source profile must list its sources"
    names = {entry["name"] for entry in attributions}
    assert len(names) >= 2, f"expected several sources, got {names}"
    for entry in attributions:
        assert entry["name"] and entry["publisher"] and entry["url"]
    # The singular field must not assert that one source supports the page.
    assert "source_attribution" not in payload


@pytest.mark.integration
def test_a_single_source_profile_keeps_its_attribution(
    session: Session, tmp_path: Path
) -> None:
    """The other side: one source is honestly one source."""
    _prepare_for_publication(session, tmp_path)
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")

    outcome = publish_wikidata_event(session, store=store)

    manifest = session.get(PublicationManifest, outcome.manifest_id)
    assert manifest is not None
    payload = store.read(manifest.storage_uri, manifest.content_hash)
    attributions = payload.get("source_attributions")
    assert attributions is not None
    assert len(attributions) == 1
    rebuild_coverage_index(session)
