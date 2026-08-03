"""Wikidata enrichment defers on a recorded-event collision (Golden 100 arc).

The committed Wikidata fixture Q749610 *is* the 1964 Alaska earthquake, which the
USGS golden profile already publishes as a recorded event on 1964-03-27. So an
enrichment attempt for that candidate must recognise the collision and defer to a
human merge review rather than publishing a competing recorded event -- the
archive must never show two recorded events fighting over one date, and D038 says
an automated pass asks the human, it never overrules one.

This first slice does only the collision-detect-and-defer; resolving the
candidate into an Event and publishing it is a later slice, which will call the
same `published_recorded_event_on` check first.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import main
from app.adapters.base import LocalFilesystemRawSourceStore
from app.coverage import rebuild_coverage_index
from app.database import get_session
from app.models import DayProfile, Event, PublicationManifest, ReviewTask
from app.wikidata import (
    ENTITY_ID,
    attempt_wikidata_enrichment,
    ingest_wikidata_candidate,
)

from .test_usgs_vertical_slice import override_session, publish

GOLDEN_DATE = date(1964, 3, 27)
WIKIDATA_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "fixtures"
    / "wikidata"
    / f"{ENTITY_ID}.json"
)
MERGE_REVIEW_PREFIX = "MERGE-REVIEW:"


def _ingest_candidate(session: Session, tmp_path: Path) -> None:
    ingest_wikidata_candidate(
        session,
        fixture_path=WIKIDATA_FIXTURE,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "wikidata-raw"),
    )


def _merge_review_tasks(session: Session) -> list[ReviewTask]:
    return list(
        session.scalars(
            select(ReviewTask).where(
                ReviewTask.rationale.like(f"{MERGE_REVIEW_PREFIX}%")
            )
        )
    )


@pytest.mark.integration
def test_wikidata_enrichment_defers_and_does_not_double_publish(
    session: Session, tmp_path: Path
) -> None:
    _, golden = publish(session, tmp_path)
    _ingest_candidate(session, tmp_path)
    rebuild_coverage_index(session)
    session.flush()

    events_before = session.scalar(select(func.count()).select_from(Event))
    manifests_before = session.scalar(
        select(func.count()).select_from(PublicationManifest)
    )
    profiles_before = session.scalar(select(func.count()).select_from(DayProfile))
    golden_manifest_before = session.get(
        PublicationManifest, golden.publication_manifest_id
    )
    assert golden_manifest_before is not None
    hash_before = golden_manifest_before.content_hash

    outcome = attempt_wikidata_enrichment(session)

    assert outcome.status == "deferred_to_merge_review"
    assert outcome.occurrence_date == GOLDEN_DATE
    assert outcome.colliding_manifest_id == golden.publication_manifest_id
    assert outcome.merge_review_task_id is not None

    # No competing recorded event is created, and the published golden profile is
    # untouched -- byte-for-byte the same manifest a reader is served.
    assert session.scalar(select(func.count()).select_from(Event)) == events_before
    assert (
        session.scalar(select(func.count()).select_from(PublicationManifest))
        == manifests_before
    )
    assert (
        session.scalar(select(func.count()).select_from(DayProfile))
        == profiles_before
    )
    golden_manifest_after = session.get(
        PublicationManifest, golden.publication_manifest_id
    )
    assert golden_manifest_after is not None
    assert golden_manifest_after.content_hash == hash_before

    # The deferral is recorded as exactly one open merge-review task.
    merge_tasks = _merge_review_tasks(session)
    assert len(merge_tasks) == 1
    assert merge_tasks[0].id == outcome.merge_review_task_id
    assert merge_tasks[0].status == "open"

    # ... and it surfaces to a human through the existing admin review surface.
    main.app.dependency_overrides[get_session] = override_session(session)
    try:
        response = TestClient(main.app).get(
            "/api/v1/admin/review-tasks",
            headers={
                "X-Development-Review-Token": main.settings.development_review_token
            },
        )
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 200
    task_ids = {task["task_id"] for task in response.json()["tasks"]}
    assert str(outcome.merge_review_task_id) in task_ids


@pytest.mark.integration
def test_wikidata_enrichment_is_idempotent(
    session: Session, tmp_path: Path
) -> None:
    publish(session, tmp_path)
    _ingest_candidate(session, tmp_path)
    rebuild_coverage_index(session)
    session.flush()

    first = attempt_wikidata_enrichment(session)
    second = attempt_wikidata_enrichment(session)

    assert first.status == "deferred_to_merge_review"
    assert second.merge_review_task_id == first.merge_review_task_id
    assert len(_merge_review_tasks(session)) == 1


@pytest.mark.integration
def test_wikidata_enrichment_no_collision_when_date_unpublished(
    session: Session, tmp_path: Path
) -> None:
    # Candidates ingested, but the golden recorded event is never published: the
    # guard keys on a *published recorded event*, not on the candidate existing.
    _ingest_candidate(session, tmp_path)
    rebuild_coverage_index(session)
    session.flush()

    outcome = attempt_wikidata_enrichment(session)

    assert outcome.status == "no_collision"
    assert outcome.occurrence_date == GOLDEN_DATE
    assert outcome.merge_review_task_id is None
    assert outcome.colliding_manifest_id is None
    assert _merge_review_tasks(session) == []


@pytest.mark.integration
def test_wikidata_enrichment_does_not_resurrect_a_reviewed_task(
    session: Session, tmp_path: Path
) -> None:
    # A human decision on the identity claim closes the merge-review task and
    # makes the claim terminal (no further decision possible). Re-running
    # enrichment must recognise that completed review, not create a fresh open
    # task the claim-decision endpoint can never close.
    from app.governance import ReviewDecisionValue, record_claim_review
    from app.models import Claim

    publish(session, tmp_path)
    _ingest_candidate(session, tmp_path)
    rebuild_coverage_index(session)
    session.flush()

    first = attempt_wikidata_enrichment(session)
    assert first.status == "deferred_to_merge_review"

    identity = session.scalars(
        select(Claim).where(Claim.claim_type == "candidate_event_identity")
    ).one()
    record_claim_review(
        session,
        claim=identity,
        decision=ReviewDecisionValue.ACCEPTED,
        rationale="Same event as the published USGS golden; merge handled in a later slice.",
        reviewed_by="test-human",
    )
    session.flush()

    second = attempt_wikidata_enrichment(session)

    assert second.status == "merge_review_resolved"
    assert second.colliding_manifest_id == first.colliding_manifest_id
    assert second.merge_review_task_id is None
    # The reviewed task remains (now closed); no new open task was resurrected.
    all_merge_tasks = _merge_review_tasks(session)
    assert len(all_merge_tasks) == 1
    assert [task for task in all_merge_tasks if task.status == "open"] == []
