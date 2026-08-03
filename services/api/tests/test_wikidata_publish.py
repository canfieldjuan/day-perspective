"""Publish a resolved Wikidata candidate as a recorded event (Golden 100 / G2b).

G2a turns a reviewed candidate into a canonical ``Event``; this slice publishes
that event as a recorded-event profile through the same source-agnostic publish
spine the USGS golden profile uses -- off ``GOLDEN_DATE`` and the ``ProfileType``
literal, with the date and profile type derived from the event's own occurrence.

Two behaviours are proven offline against the committed ``Q749610`` fixture:

* On a date that holds no recorded event, the resolved candidate publishes and
  the date reads ``ENRICHED`` with ``has_recorded_event`` set.
* On a date that already publishes a recorded event (the USGS golden date, which
  ``Q749610`` shares), publication defers to a durable merge-review task and
  never mints a competing event or profile (G1's collision guard, now enforced
  at the publish boundary).

The pass consumes the human stages before it -- claim acceptance (D019) and
editorial ranking -- and fabricates neither (D038): it publishes only what a
human accepted and ranked, and defers rather than overrule a human on a
collision.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.base import LocalFilesystemRawSourceStore
from app.coverage import coverage_entry, rebuild_coverage_index
from app.governance import (
    EditorialSelectionStatus,
    ReviewDecisionValue,
    record_claim_review,
    record_editorial_selection,
)
from app.models import (
    Claim,
    DayProfile,
    Event,
    ProfileType,
    PublicationManifest,
    PublicationTier,
    ResolvedClaim,
    ReviewTask,
)
from app.services import LocalFilesystemPublishedProfileStore
from app.wikidata import (
    ENTITY_ID,
    ingest_wikidata_candidate,
    publish_wikidata_event,
    resolve_wikidata_event,
)

from .test_usgs_vertical_slice import publish as publish_golden

GOLDEN_DATE = date(1964, 3, 27)
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
#: The predicates whose reviewed candidate becomes a published recorded-event
#: statement (identity is provenance, not a reader-facing statement).
PUBLISHED_PREDICATES = (
    "candidate_name",
    "candidate_event_type",
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


def _resolved(session: Session, predicate: str) -> ResolvedClaim | None:
    return session.scalars(
        select(ResolvedClaim)
        .where(ResolvedClaim.canonical_key == f"wikidata:{ENTITY_ID}:{predicate}")
        .order_by(ResolvedClaim.version.desc())
    ).first()


def _editorial_rank(session: Session, profile_date: date) -> None:
    """The human editorial-ranking stage the publish pass consumes.

    A person selects the reviewed resolved candidates for the recorded-event
    section; the pass never records this on their behalf (D038).
    """
    for rank, predicate in enumerate(PUBLISHED_PREDICATES, start=1):
        resolved = _resolved(session, predicate)
        assert resolved is not None
        record_editorial_selection(
            session,
            profile_date=profile_date,
            section_key="recorded_on_this_date",
            resolved_claim_id=resolved.id,
            status=EditorialSelectionStatus.SELECTED,
            display_rank=rank,
            rationale="Human editorial ranking of the reviewed Wikidata candidate.",
            reviewed_by="test-human",
        )


def _prepare_for_publication(session: Session, tmp_path: Path) -> None:
    _ingest(session, tmp_path)
    _accept_core(session)
    resolve_wikidata_event(session)
    _editorial_rank(session, GOLDEN_DATE)


@pytest.mark.integration
def test_publish_marks_the_date_enriched_from_the_resolved_candidate(
    session: Session, tmp_path: Path
) -> None:
    _prepare_for_publication(session, tmp_path)
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")

    outcome = publish_wikidata_event(session, store=store)

    assert outcome.status == "published"
    assert outcome.occurrence_date == GOLDEN_DATE
    manifest = session.get(PublicationManifest, outcome.manifest_id)
    assert manifest is not None
    # Date and profile type are derived from the occurrence, not hardcoded.
    assert manifest.profile_date == GOLDEN_DATE
    assert manifest.profile_type is ProfileType.STANDARD_STATISTICAL
    assert manifest.publication_tier is PublicationTier.ENRICHED

    # The recorded event makes the date ENRICHED in the reader-facing index.
    entry = coverage_entry(session, GOLDEN_DATE)
    assert entry is not None
    assert entry.has_recorded_event is True
    assert entry.publication_tier is PublicationTier.ENRICHED

    # Statement text derives only from resolved data (honest-data): the occurrence
    # statement names the parsed P585 date, and the title carries the label.
    payload = store.read(manifest.storage_uri, manifest.content_hash)
    recorded = payload["sections"]["recorded_on_this_date"]
    assert len(recorded) == len(PUBLISHED_PREDICATES)
    label = (_claim(session, "candidate_name").assertion_json or {})["value"]["label"]
    assert any(label in item["statement"] for item in recorded)
    assert any("March 27, 1964" in item["statement"] for item in recorded)
    for item in recorded:
        assert item["provenance"]["resolved_claim"]["canonical_key"].startswith(
            f"wikidata:{ENTITY_ID}:"
        )


@pytest.mark.integration
def test_publish_defers_on_recorded_event_collision(
    session: Session, tmp_path: Path
) -> None:
    # The USGS golden recorded event already owns 1964-03-27, which Q749610 shares.
    _, golden = publish_golden(session, tmp_path)
    _ingest(session, tmp_path)
    _accept_core(session)
    rebuild_coverage_index(session)
    session.flush()
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")

    manifests_before = session.scalar(
        select(func.count()).select_from(PublicationManifest)
    )
    profiles_before = session.scalar(select(func.count()).select_from(DayProfile))
    events_before = session.scalar(select(func.count()).select_from(Event))
    golden_manifest = session.get(
        PublicationManifest, golden.publication_manifest_id
    )
    assert golden_manifest is not None
    hash_before = golden_manifest.content_hash

    outcome = publish_wikidata_event(session, store=store)

    assert outcome.status == "deferred_to_merge_review"
    assert outcome.occurrence_date == GOLDEN_DATE
    assert outcome.colliding_manifest_id == golden.publication_manifest_id

    # No competing recorded event: nothing new published, no Wikidata event minted,
    # and the served golden profile is byte-for-byte unchanged.
    assert (
        session.scalar(select(func.count()).select_from(PublicationManifest))
        == manifests_before
    )
    assert (
        session.scalar(select(func.count()).select_from(DayProfile))
        == profiles_before
    )
    assert session.scalar(select(func.count()).select_from(Event)) == events_before
    golden_after = session.get(PublicationManifest, golden.publication_manifest_id)
    assert golden_after is not None
    assert golden_after.content_hash == hash_before

    # Exactly one open merge-review task, on the identity claim, carrying the
    # sentinel a later merge/supersede/distinct-event decision resolves.
    identity = _claim(session, "candidate_event_identity")
    tasks = list(
        session.scalars(
            select(ReviewTask).where(
                ReviewTask.claim_id == identity.id,
                ReviewTask.status == "open",
            )
        )
    )
    assert len(tasks) == 1
    assert tasks[0].rationale.startswith("MERGE-REVIEW:")
    assert outcome.merge_review_task_id == tasks[0].id


@pytest.mark.integration
def test_publish_defer_is_idempotent(session: Session, tmp_path: Path) -> None:
    publish_golden(session, tmp_path)
    _ingest(session, tmp_path)
    _accept_core(session)
    rebuild_coverage_index(session)
    session.flush()
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")

    first = publish_wikidata_event(session, store=store)
    second = publish_wikidata_event(session, store=store)

    assert first.status == second.status == "deferred_to_merge_review"
    assert first.merge_review_task_id == second.merge_review_task_id
    identity = _claim(session, "candidate_event_identity")
    assert (
        session.scalar(
            select(func.count())
            .select_from(ReviewTask)
            .where(
                ReviewTask.claim_id == identity.id,
                ReviewTask.status == "open",
            )
        )
        == 1
    )


@pytest.mark.integration
def test_publish_is_idempotent(session: Session, tmp_path: Path) -> None:
    _prepare_for_publication(session, tmp_path)
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")

    first = publish_wikidata_event(session, store=store)
    second = publish_wikidata_event(session, store=store)

    assert first.status == second.status == "published"
    assert first.manifest_id == second.manifest_id
    assert session.scalar(select(func.count()).select_from(PublicationManifest)) == 1
    assert session.scalar(select(func.count()).select_from(DayProfile)) == 1


@pytest.mark.integration
def test_publish_requires_accepted_candidates(
    session: Session, tmp_path: Path
) -> None:
    # D019: an unreviewed candidate cannot be published.
    _ingest(session, tmp_path)
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")

    with pytest.raises(ValueError):
        publish_wikidata_event(session, store=store)

    assert session.scalar(select(func.count()).select_from(PublicationManifest)) == 0
    assert session.scalar(select(func.count()).select_from(DayProfile)) == 0


@pytest.mark.integration
def test_publish_requires_human_editorial_ranking(
    session: Session, tmp_path: Path
) -> None:
    # The pass consumes editorial ranking; it never fabricates it (D038). With the
    # candidate accepted and resolved but not ranked, publication is refused.
    _ingest(session, tmp_path)
    _accept_core(session)
    resolve_wikidata_event(session)
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")

    with pytest.raises(ValueError):
        publish_wikidata_event(session, store=store)

    assert session.scalar(select(func.count()).select_from(PublicationManifest)) == 0
    assert session.scalar(select(func.count()).select_from(DayProfile)) == 0
