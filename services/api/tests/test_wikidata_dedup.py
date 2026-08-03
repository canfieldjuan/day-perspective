"""Wikidata enrichment defers on a recorded-event collision (Golden 100 arc).

The committed Wikidata fixture Q749610 *is* the 1964 Alaska earthquake, which the
USGS golden profile already publishes as a recorded event on 1964-03-27. So an
enrichment attempt for that candidate must recognise the collision and defer,
never publishing a competing recorded event on a date that already holds one.

This first slice is a pure detector: it reports the collision (so a later slice
never publishes over it) and writes nothing. The durable, resolvable merge-review
record belongs with the merge/supersede/distinct-event lifecycle in a later slice.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.base import LocalFilesystemRawSourceStore
from app.coverage import rebuild_coverage_index
from app.models import DayProfile, Event, PublicationManifest, ReviewTask
from app.wikidata import (
    ENTITY_ID,
    attempt_wikidata_enrichment,
    ingest_wikidata_candidate,
)

from .test_usgs_vertical_slice import publish

GOLDEN_DATE = date(1964, 3, 27)
WIKIDATA_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "fixtures"
    / "wikidata"
    / f"{ENTITY_ID}.json"
)


def _ingest_candidate(session: Session, tmp_path: Path) -> None:
    ingest_wikidata_candidate(
        session,
        fixture_path=WIKIDATA_FIXTURE,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "wikidata-raw"),
    )


def _write_counts(session: Session) -> dict[str, int | None]:
    """Row counts the detector must leave untouched -- it writes nothing."""
    return {
        "events": session.scalar(select(func.count()).select_from(Event)),
        "manifests": session.scalar(
            select(func.count()).select_from(PublicationManifest)
        ),
        "profiles": session.scalar(select(func.count()).select_from(DayProfile)),
        "review_tasks": session.scalar(select(func.count()).select_from(ReviewTask)),
    }


@pytest.mark.integration
def test_wikidata_enrichment_defers_on_collision_and_writes_nothing(
    session: Session, tmp_path: Path
) -> None:
    _, golden = publish(session, tmp_path)
    _ingest_candidate(session, tmp_path)
    rebuild_coverage_index(session)
    session.flush()

    counts_before = _write_counts(session)
    golden_manifest_before = session.get(
        PublicationManifest, golden.publication_manifest_id
    )
    assert golden_manifest_before is not None
    hash_before = golden_manifest_before.content_hash

    outcome = attempt_wikidata_enrichment(session)

    assert outcome.status == "deferred_to_merge_review"
    assert outcome.occurrence_date == GOLDEN_DATE
    assert outcome.colliding_manifest_id == golden.publication_manifest_id

    # A pure detector: no competing event, and nothing at all is written -- the
    # published golden profile is byte-for-byte the manifest a reader is served.
    assert _write_counts(session) == counts_before
    golden_manifest_after = session.get(
        PublicationManifest, golden.publication_manifest_id
    )
    assert golden_manifest_after is not None
    assert golden_manifest_after.content_hash == hash_before


@pytest.mark.integration
def test_wikidata_enrichment_is_idempotent_and_side_effect_free(
    session: Session, tmp_path: Path
) -> None:
    publish(session, tmp_path)
    _ingest_candidate(session, tmp_path)
    rebuild_coverage_index(session)
    session.flush()

    counts_before = _write_counts(session)
    first = attempt_wikidata_enrichment(session)
    second = attempt_wikidata_enrichment(session)

    # Read-only, so repeated runs give the same answer and write nothing --
    # idempotent by construction, no task to serialize or resurrect.
    assert first == second
    assert first.status == "deferred_to_merge_review"
    assert _write_counts(session) == counts_before


@pytest.mark.integration
def test_wikidata_enrichment_no_collision_when_date_unpublished(
    session: Session, tmp_path: Path
) -> None:
    # Candidates ingested, but the golden recorded event is never published: the
    # detector keys on a *published recorded event*, not on the candidate existing.
    _ingest_candidate(session, tmp_path)
    rebuild_coverage_index(session)
    session.flush()

    outcome = attempt_wikidata_enrichment(session)

    assert outcome.status == "no_collision"
    assert outcome.occurrence_date == GOLDEN_DATE
    assert outcome.colliding_manifest_id is None
