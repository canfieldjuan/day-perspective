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

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.base import LocalFilesystemRawSourceStore
from app.coverage import coverage_entry, rebuild_coverage_index
from app.governance import (
    EditorialSelectionStatus,
    EventIdentityAdjudication,
    IdentityAdjudicationDecision,
    IdentityAdjudicationError,
    ReviewDecisionValue,
    adjudicated_distinct,
    events_behind_manifest,
    record_claim_review,
    record_editorial_selection,
    record_featured_event_selection,
    record_identity_adjudication,
)
from app.models import (
    Claim,
    ClaimAssertionStatus,
    DayProfile,
    Event,
    EventTime,
    LegalReviewStatus,
    ProfileType,
    PublicationManifest,
    PublicationStatementEvidence,
    PublicationStatus,
    PublicationTier,
    ResolvedClaim,
    ReviewTask,
    Source,
    SourceRelease,
)
from app.services import (
    LocalFilesystemPublishedProfileStore,
    PublicationStatementEvidenceInput,
    RecordedEventBinding,
    create_claim,
    create_source_release,
    publish_day_profile,
)
from app.wikidata import (
    ENTITY_ID,
    _collision_adjudication,
    ingest_wikidata_candidate,
    publish_wikidata_event,
    resolve_merge_review,
    resolve_wikidata_event,
)

from .test_identity_adjudication import _make_event
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


def _wikidata_event(session: Session) -> Event:
    """The canonical Event the Wikidata candidate resolved into."""
    identity = _resolved(session, "candidate_event_identity")
    assert identity is not None
    event = session.scalar(
        select(Event).where(Event.resolved_claim_id == identity.id)
    )
    assert event is not None
    return event


def _usgs_event_id(session: Session, manifest: PublicationManifest) -> UUID:
    """The single canonical event a golden manifest publishes."""
    behind = events_behind_manifest(session, manifest=manifest)
    assert len(behind) == 1
    return next(iter(behind))


def _current_recorded(
    session: Session, store: LocalFilesystemPublishedProfileStore
) -> tuple[PublicationManifest, list[dict[str, Any]], list[UUID], set[UUID]]:
    """The date's current recorded section, its roots, and the events behind it."""
    manifest = session.scalar(
        select(PublicationManifest)
        .where(
            PublicationManifest.profile_date == GOLDEN_DATE,
            PublicationManifest.status == PublicationStatus.PUBLISHED,
        )
        .order_by(PublicationManifest.version.desc())
    )
    assert manifest is not None
    payload = store.read(manifest.storage_uri, manifest.content_hash)
    statements = list(payload["sections"]["recorded_on_this_date"])
    roots: list[UUID] = []
    for index in range(len(statements)):
        root = session.scalar(
            select(PublicationStatementEvidence.resolved_claim_id).where(
                PublicationStatementEvidence.publication_manifest_id == manifest.id,
                PublicationStatementEvidence.statement_path
                == f"/sections/recorded_on_this_date/{index}",
            )
        )
        assert root is not None
        roots.append(root)
    return manifest, statements, roots, events_behind_manifest(
        session, manifest=manifest
    )


def _republish_with_a_different_event(
    session: Session,
    store: LocalFilesystemPublishedProfileStore,
    *,
    key: str,
) -> Event:
    """Add an unrelated event to the date, changing the collision's event set.

    The date's recorded event cannot simply be *replaced* -- publication refuses
    to drop an event a version already admitted, which is the whole point of the
    binding. What can happen is the set growing, and that is equally enough to
    make a waiting merge-review task's subject no longer the collision the date
    holds.
    """
    stranger = _make_event(session, key=key)
    occurrence = session.scalar(
        select(EventTime.provenance_resolved_claim_id).where(
            EventTime.event_id == stranger.id, EventTime.is_primary.is_(True)
        )
    )
    assert occurrence is not None
    manifest, statements, roots, existing = _current_recorded(session, store)
    assert len(existing) == 1
    incumbent = next(iter(existing))
    payload_statements = statements + [
        {
            "statement_id": key,
            "statement": "Another recorded event now shares this date.",
            "details": {},
            "provenance_note": "development fixture",
        }
    ]
    publish_day_profile(
        session,
        store=store,
        profile_date=GOLDEN_DATE,
        profile_type=manifest.profile_type,
        payload={
            "schema_version": "1",
            "date": GOLDEN_DATE.isoformat(),
            "profile_type": manifest.profile_type.value,
            "sections": {"recorded_on_this_date": payload_statements},
            "section_states": {"recorded_on_this_date": {"status": "available"}},
        },
        statement_evidence=[
            PublicationStatementEvidenceInput(
                statement_path=f"/sections/recorded_on_this_date/{index}",
                resolved_claim_id=root,
            )
            for index, root in enumerate([*roots, occurrence])
        ],
        recorded_events=[
            RecordedEventBinding(
                event_id=incumbent,
                is_featured=True,
                featured_selection_id=None,
                statement_count=len(statements),
            ),
            RecordedEventBinding(
                event_id=stranger.id,
                is_featured=False,
                featured_selection_id=None,
                statement_count=1,
            ),
        ],
        force_new_version=True,
    )
    rebuild_coverage_index(session)
    session.flush()
    return stranger


def _feature_the_wikidata_event(session: Session) -> None:
    """A human picks the headline for a date that now admits two events.

    Publishing a multi-event date requires a human's choice (G3b-1); the
    deterministic default is a later slice. These tests are about the collision
    guard, not about who leads, so they supply the choice rather than assert it
    is unnecessary.
    """
    wikidata = _wikidata_event(session)
    candidates = [wikidata.resolved_claim_id] + [
        event.resolved_claim_id
        for event in session.scalars(select(Event))
        if event.id != wikidata.id
    ]
    record_featured_event_selection(
        session,
        profile_date=GOLDEN_DATE,
        candidate_root_ids=candidates,
        chosen_root_id=wikidata.resolved_claim_id,
        reviewer="test-human",
        rationale="Featured so the date has one headline.",
    )


def _prepare_for_publication(session: Session, tmp_path: Path) -> None:
    _ingest(session, tmp_path)
    _accept_core(session)
    resolve_wikidata_event(session)
    _editorial_rank(session, GOLDEN_DATE)


def _publish_prior_context(
    session: Session, store: LocalFilesystemPublishedProfileStore
) -> DayProfile:
    """A minimal context-only profile already on the date, as archive activation
    would have published it -- so enrichment must preserve it, not replace it."""
    occurrence = _resolved(session, "candidate_occurrence_date")
    assert occurrence is not None
    payload = {
        "schema_version": "1",
        "date": GOLDEN_DATE.isoformat(),
        "profile_type": ProfileType.STANDARD_STATISTICAL.value,
        "sections": {
            "typical_day_in_this_year": [
                {
                    "statement_id": "annual-context-fixture",
                    "statement": "Annual context statement for this test.",
                    "details": {"note": "annual context, not date-specific"},
                    "provenance_note": "development fixture context",
                }
            ]
        },
        "section_states": {"typical_day_in_this_year": {"status": "available"}},
    }
    return publish_day_profile(
        session,
        store=store,
        profile_date=GOLDEN_DATE,
        profile_type=ProfileType.STANDARD_STATISTICAL,
        payload=payload,
        statement_evidence=[
            PublicationStatementEvidenceInput(
                statement_path="/sections/typical_day_in_this_year/0",
                resolved_claim_id=occurrence.id,
            )
        ],
    )


def _reingest(
    session: Session,
    *,
    label: str = "1964 event",
    occurrence_time: str = "+1964-03-27T00:00:00Z",
) -> SourceRelease:
    """A newer release for the same entity, accepted but not re-resolved.

    Mirrors a live re-ingest that the pinned offline fixture cannot produce: the
    candidate is accepted on the new release, but the resolution still rests on the
    original one -- the case where publication must bind to the resolution, not the
    newest release.
    """
    source = session.scalar(
        select(Source).where(Source.slug == "wikidata-candidates")
    )
    assert source is not None
    release = create_source_release(
        session,
        source_id=source.id,
        release_label="wikidata-Q749610-reingest",
        source_url="https://www.wikidata.org/wiki/Q749610?oldid=999",
        raw_storage_uri="memory://reingest",
        raw_record_count=1,
        raw_bytes=b"reingest-fixture",
        legal_review_status=LegalReviewStatus.NOT_REQUIRED,
    )
    session.flush()
    values = {
        "candidate_event_identity": {
            "entity_id": ENTITY_ID,
            "pageid": 1,
            "revision_id": 999,
        },
        "candidate_event_type": {"id": "Q7944"},
        "candidate_name": {"label": label, "aliases": []},
        "candidate_occurrence_date": {
            "time": occurrence_time,
            "precision": 11,
        },
    }
    for claim_type, value in values.items():
        claim = create_claim(
            session,
            source_release_id=release.id,
            source_record_locator="https://www.wikidata.org/wiki/Q749610?oldid=999",
            source_record_hash_sha256="0" * 64,
            claim_type=claim_type,
            assertion_text=json.dumps(value, sort_keys=True),
            assertion_json={
                "value": value,
                "wikidata_reference_count": 0,
                "candidate_only": True,
            },
            assertion_status=ClaimAssertionStatus.CANDIDATE,
        )
        record_claim_review(
            session,
            claim=claim,
            decision=ReviewDecisionValue.ACCEPTED,
            rationale="Re-ingested candidate reviewed for this test.",
            reviewed_by="test-human",
        )
    session.flush()
    return release


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
def test_publish_exposes_recorded_event_temporal_qualification(
    session: Session, tmp_path: Path
) -> None:
    # A directly recorded event must display its temporal precision, assignment,
    # and date role (docs/PRODUCT_CONTRACT.md recorded-event rules) -- sourced from
    # the resolved EventTime, not just the raw Wikidata time object.
    _prepare_for_publication(session, tmp_path)
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")

    outcome = publish_wikidata_event(session, store=store)

    manifest = session.get(PublicationManifest, outcome.manifest_id)
    assert manifest is not None
    payload = store.read(manifest.storage_uri, manifest.content_hash)
    recorded = payload["sections"]["recorded_on_this_date"]
    occurrence = next(
        item for item in recorded if item["statement_id"] == "wikidata-occurrence-date"
    )
    assert occurrence["details"]["temporal_precision"] == "day"
    assert occurrence["details"]["temporal_assignment"] == "reported"
    assert occurrence["details"]["date_role"] == "occurred"
    # Every recorded statement discloses its claim's data state (contract §71-74).
    for item in recorded:
        assert item["details"]["data_status"] == "reported"


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


@pytest.mark.integration
def test_publish_requires_the_occurrence_selection(
    session: Session, tmp_path: Path
) -> None:
    # A recorded event must display its occurrence's temporal qualification
    # (docs/PRODUCT_CONTRACT.md), so a selection that omits the occurrence root --
    # even if other roots are selected -- cannot publish.
    _ingest(session, tmp_path)
    _accept_core(session)
    resolve_wikidata_event(session)
    name = _resolved(session, "candidate_name")
    assert name is not None
    record_editorial_selection(
        session,
        profile_date=GOLDEN_DATE,
        section_key="recorded_on_this_date",
        resolved_claim_id=name.id,
        status=EditorialSelectionStatus.SELECTED,
        display_rank=1,
        rationale="Only the name is ranked, not the occurrence.",
        reviewed_by="test-human",
    )
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")

    with pytest.raises(ValueError):
        publish_wikidata_event(session, store=store)

    assert session.scalar(select(func.count()).select_from(PublicationManifest)) == 0
    assert session.scalar(select(func.count()).select_from(DayProfile)) == 0


@pytest.mark.integration
def test_publish_enriches_without_dropping_existing_context(
    session: Session, tmp_path: Path
) -> None:
    # A date that already carries annual context must be *enriched*, not replaced:
    # adding the recorded event preserves the existing sections (P1).
    _prepare_for_publication(session, tmp_path)
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    context = _publish_prior_context(session, store)
    before = coverage_entry(session, GOLDEN_DATE)
    assert before is not None and before.has_recorded_event is False

    outcome = publish_wikidata_event(session, store=store)

    assert outcome.status == "published"
    assert outcome.manifest_id != context.publication_manifest_id
    manifest = session.get(PublicationManifest, outcome.manifest_id)
    assert manifest is not None
    payload = store.read(manifest.storage_uri, manifest.content_hash)
    # The recorded event was added AND the prior annual context survived.
    assert payload["sections"]["recorded_on_this_date"]
    assert payload["sections"]["typical_day_in_this_year"]

    entry = coverage_entry(session, GOLDEN_DATE)
    assert entry is not None
    assert entry.has_recorded_event is True
    assert entry.publication_tier is PublicationTier.ENRICHED
    # The enriched version supersedes the context profile as the served one.
    assert entry.publication_manifest_id == outcome.manifest_id


@pytest.mark.integration
def test_publish_orders_statements_by_editorial_rank(
    session: Session, tmp_path: Path
) -> None:
    # The reader-visible order follows the human editorial ranking, not the source
    # predicate order (P2): rank the predicates in reverse and expect that order.
    _ingest(session, tmp_path)
    _accept_core(session)
    resolve_wikidata_event(session)
    reversed_predicates = list(reversed(PUBLISHED_PREDICATES))
    for rank, predicate in enumerate(reversed_predicates, start=1):
        resolved = _resolved(session, predicate)
        assert resolved is not None
        record_editorial_selection(
            session,
            profile_date=GOLDEN_DATE,
            section_key="recorded_on_this_date",
            resolved_claim_id=resolved.id,
            status=EditorialSelectionStatus.SELECTED,
            display_rank=rank,
            rationale="Reverse-order editorial ranking for this test.",
            reviewed_by="test-human",
        )
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")

    outcome = publish_wikidata_event(session, store=store)

    manifest = session.get(PublicationManifest, outcome.manifest_id)
    assert manifest is not None
    payload = store.read(manifest.storage_uri, manifest.content_hash)
    recorded = payload["sections"]["recorded_on_this_date"]
    expected_ids = [
        predicate.replace("candidate_", "wikidata-").replace("_", "-")
        for predicate in reversed_predicates
    ]
    assert [item["statement_id"] for item in recorded] == expected_ids


@pytest.mark.integration
def test_publish_binds_provenance_to_the_resolution_not_the_latest_release(
    session: Session, tmp_path: Path
) -> None:
    # A re-ingest adds a newer release with a changed name, but the resolution is
    # not re-run. Published statement text and provenance must reflect the
    # resolution the event rests on, never the newer, unrelated record.
    _prepare_for_publication(session, tmp_path)
    original_label = (_claim(session, "candidate_name").assertion_json or {})[
        "value"
    ]["label"]
    original_release = session.scalars(
        select(SourceRelease).order_by(SourceRelease.ingested_at)
    ).first()
    assert original_release is not None
    _reingest(session, label="REINGESTED-CHANGED-NAME")
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")

    outcome = publish_wikidata_event(session, store=store)

    manifest = session.get(PublicationManifest, outcome.manifest_id)
    assert manifest is not None
    payload = store.read(manifest.storage_uri, manifest.content_hash)
    name_statement = next(
        item
        for item in payload["sections"]["recorded_on_this_date"]
        if item["statement_id"] == "wikidata-name"
    )
    assert "REINGESTED-CHANGED-NAME" not in name_statement["statement"]
    assert original_label in name_statement["statement"]
    assert (
        name_statement["provenance"]["source_release"]["release"]
        == original_release.release_label
    )


@pytest.mark.integration
def test_publish_checks_collision_on_the_resolved_date_not_the_reingested_candidate(
    session: Session, tmp_path: Path
) -> None:
    # The collision guard must fire on the date publication actually targets -- the
    # resolved occurrence -- not on a re-ingested candidate that moved P585. Here
    # the resolved date holds the USGS golden recorded event; a re-ingest points the
    # candidate at a different, collision-free date, but publication still targets
    # the resolved date and must defer rather than overwrite the golden section.
    _ingest(session, tmp_path)
    _accept_core(session)
    resolve_wikidata_event(session)
    _, golden = publish_golden(session, tmp_path)
    rebuild_coverage_index(session)
    session.flush()
    _reingest(session, occurrence_time="+1970-01-15T00:00:00Z")
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")

    golden_manifest_before = session.get(
        PublicationManifest, golden.publication_manifest_id
    )
    assert golden_manifest_before is not None
    hash_before = golden_manifest_before.content_hash

    outcome = publish_wikidata_event(session, store=store)

    assert outcome.status == "deferred_to_merge_review"
    assert outcome.occurrence_date == GOLDEN_DATE
    assert outcome.colliding_manifest_id == golden.publication_manifest_id
    # The golden recorded event is untouched -- no competing publish on its date.
    golden_after = session.get(
        PublicationManifest, golden.publication_manifest_id
    )
    assert golden_after is not None
    assert golden_after.content_hash == hash_before


@pytest.mark.integration
def test_a_human_distinct_event_decision_lets_publication_pass_the_collision(
    session: Session, tmp_path: Path
) -> None:
    """The whole point of the adjudication: the human's answer reaches the guard.

    Before this slice the merge-review task could be closed and nothing changed --
    the next attempt collided and deferred again, because the guard had no record
    to read.
    """
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
        reviewer="test-human",
        rationale="Two different events that share 1964-03-27.",
    )
    _feature_the_wikidata_event(session)
    session.flush()

    published = publish_wikidata_event(session, store=store)

    assert published.status == "published"
    assert published.occurrence_date == GOLDEN_DATE
    assert published.manifest_id is not None
    # The task that asked the question is closed, and the answer is durable.
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
        == 0
    )


@pytest.mark.integration
def test_a_decision_about_another_pair_does_not_unlock_this_collision(
    session: Session, tmp_path: Path
) -> None:
    """Pair-specific: adjudicating against some other event is not permission."""
    publish_golden(session, tmp_path)
    _prepare_for_publication(session, tmp_path)
    rebuild_coverage_index(session)
    session.flush()
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    unrelated = _make_event(session, key="unrelated-on-the-golden-date")

    record_identity_adjudication(
        session,
        event_a_id=_wikidata_event(session).id,
        event_b_id=unrelated.id,
        decision=IdentityAdjudicationDecision.DISTINCT_EVENT,
        reviewer="test-human",
        rationale="Distinct from an event that is not the one colliding.",
    )
    session.flush()

    outcome = publish_wikidata_event(session, store=store)

    assert outcome.status == "deferred_to_merge_review"


@pytest.mark.integration
def test_a_claimed_merge_review_task_is_resolved_not_duplicated(
    session: Session, tmp_path: Path
) -> None:
    """A reviewer claiming the task must not fork the workflow.

    Moving a task to ``in_progress`` is how a reviewer says "I am on this". If
    only ``open`` counts as active, the next publish attempt opens a *second*
    task asking the same question, and the answer records against neither.
    """
    publish_golden(session, tmp_path)
    _prepare_for_publication(session, tmp_path)
    rebuild_coverage_index(session)
    session.flush()
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    first = publish_wikidata_event(session, store=store)
    assert first.merge_review_task_id is not None

    claimed = session.get(ReviewTask, first.merge_review_task_id)
    assert claimed is not None
    claimed.status = "in_progress"
    claimed.assigned_to = "test-human"
    session.flush()

    # A retry while the task is claimed reuses it rather than stacking another.
    again = publish_wikidata_event(session, store=store)
    assert again.merge_review_task_id == claimed.id
    identity = _claim(session, "candidate_event_identity")
    assert (
        session.scalar(
            select(func.count())
            .select_from(ReviewTask)
            .where(
                ReviewTask.claim_id == identity.id,
                ReviewTask.status.in_(("open", "in_progress")),
            )
        )
        == 1
    )

    recorded = resolve_merge_review(
        session,
        decision=IdentityAdjudicationDecision.DISTINCT_EVENT,
        reviewer="test-human",
        rationale="Resolved by the reviewer who claimed the task.",
    )
    session.flush()

    # The claimed task is linked to the decision and completed, not orphaned.
    assert recorded[0].review_task_id == claimed.id
    session.refresh(claimed)
    assert claimed.status == "resolved"
    assert claimed.completed_at is not None


@pytest.mark.integration
def test_the_adjudicate_command_records_the_decision_and_unblocks_publication(
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The operator-facing half of the workflow.

    `publish` opens a task asking whether two events are the same event; without
    a command that answers it, the collision defers forever no matter what a
    reviewer decides, and the decision is reachable only from a test.
    """
    from contextlib import nullcontext

    from app import candidate_cli

    publish_golden(session, tmp_path)
    _prepare_for_publication(session, tmp_path)
    rebuild_coverage_index(session)
    session.flush()
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    assert publish_wikidata_event(session, store=store).status == (
        "deferred_to_merge_review"
    )

    class _Settings:
        published_profile_root = tmp_path / "published"
        raw_source_root = tmp_path / "raw"

    monkeypatch.setattr(candidate_cli, "SessionLocal", lambda: nullcontext(session))
    monkeypatch.setattr(candidate_cli, "get_settings", lambda: _Settings())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "candidate_cli",
            "adjudicate",
            "--decision",
            "distinct_event",
            "--reviewer",
            "test-human",
            "--rationale",
            "Two different events that share 1964-03-27.",
        ],
    )

    candidate_cli.main()

    reported = capsys.readouterr().out
    assert "decision=distinct_event" in reported
    assert "adjudication_id=" in reported
    # And the guard now lets the second event through.
    _feature_the_wikidata_event(session)
    session.flush()
    assert publish_wikidata_event(session, store=store).status == "published"


@pytest.mark.integration
def test_the_adjudicate_command_refuses_a_standing_rule_reviewer(
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D038 holds at the operator boundary too, not only inside the writer."""
    from contextlib import nullcontext

    from app import candidate_cli

    publish_golden(session, tmp_path)
    _prepare_for_publication(session, tmp_path)
    rebuild_coverage_index(session)
    session.flush()
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    publish_wikidata_event(session, store=store)

    class _Settings:
        published_profile_root = tmp_path / "published"
        raw_source_root = tmp_path / "raw"

    monkeypatch.setattr(candidate_cli, "SessionLocal", lambda: nullcontext(session))
    monkeypatch.setattr(candidate_cli, "get_settings", lambda: _Settings())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "candidate_cli",
            "adjudicate",
            "--decision",
            "distinct_event",
            "--reviewer",
            "standing-rule:featured-event-v1",
            "--rationale",
            "A pass must not adjudicate identity.",
        ],
    )

    with pytest.raises(IdentityAdjudicationError):
        candidate_cli.main()


@pytest.mark.integration
def test_resolving_the_same_merge_review_twice_is_idempotent(
    session: Session, tmp_path: Path
) -> None:
    """A retried answer is the same answer, not a second version of it.

    The first call closes the task, so a naive retry finds no active task,
    records ``review_task_id=None``, and that difference alone appends a
    spurious version 2 -- the reviewer, decision and rationale being identical.
    """
    publish_golden(session, tmp_path)
    _prepare_for_publication(session, tmp_path)
    rebuild_coverage_index(session)
    session.flush()
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    publish_wikidata_event(session, store=store)

    first = resolve_merge_review(
        session,
        decision=IdentityAdjudicationDecision.DISTINCT_EVENT,
        reviewer="test-human",
        rationale="Two different events that share 1964-03-27.",
    )
    session.flush()
    second = resolve_merge_review(
        session,
        decision=IdentityAdjudicationDecision.DISTINCT_EVENT,
        reviewer="test-human",
        rationale="Two different events that share 1964-03-27.",
    )
    session.flush()

    assert [row.id for row in second] == [row.id for row in first]
    assert all(row.decision_version == 1 for row in second)
    assert (
        session.scalar(select(func.count()).select_from(EventIdentityAdjudication))
        == len(first)
    )
    # The linkage to the task that asked is not dropped by the retry.
    assert all(row.review_task_id is not None for row in second)


@pytest.mark.integration
def test_adjudication_refuses_when_the_date_now_publishes_a_different_event(
    session: Session, tmp_path: Path
) -> None:
    """The reviewer's answer must land on the pair the task actually asked about.

    A merge-review task names one collision. If the date is republished with a
    *different* recorded event while the task waits, resolving against whatever
    the coverage index now points at would record a durable identity decision --
    and later a publication bypass -- for events the reviewer never evaluated.
    """
    publish_golden(session, tmp_path)
    _prepare_for_publication(session, tmp_path)
    rebuild_coverage_index(session)
    session.flush()
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    deferred = publish_wikidata_event(session, store=store)
    assert deferred.status == "deferred_to_merge_review"

    # The date's recorded event is replaced by an unrelated one while the task
    # waits, so the collision the reviewer was asked about is no longer current.
    _republish_with_a_different_event(session, store, key="stranger")

    with pytest.raises(ValueError, match="no longer"):
        resolve_merge_review(
            session,
            decision=IdentityAdjudicationDecision.DISTINCT_EVENT,
            reviewer="test-human",
            rationale="Answering a question about a collision that has moved.",
        )
    # And nothing durable was recorded for the pair the reviewer never saw.
    assert (
        session.scalar(select(func.count()).select_from(EventIdentityAdjudication))
        == 0
    )


@pytest.mark.integration
def test_a_retry_after_the_collision_changed_is_refused(
    session: Session, tmp_path: Path
) -> None:
    """The retry path must check the subject too, not only the first call.

    Once the first answer closes the task, a later call finds no active task. If
    that path skips the staleness check, repeating the command after the date
    has been republished records the same ``distinct_event`` against the *new*
    pair -- a durable bypass for events nobody evaluated, reached by pressing up
    and enter.
    """
    publish_golden(session, tmp_path)
    _prepare_for_publication(session, tmp_path)
    rebuild_coverage_index(session)
    session.flush()
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    publish_wikidata_event(session, store=store)
    resolve_merge_review(
        session,
        decision=IdentityAdjudicationDecision.DISTINCT_EVENT,
        reviewer="test-human",
        rationale="Two different events that share 1964-03-27.",
    )
    session.flush()
    before = session.scalar(
        select(func.count()).select_from(EventIdentityAdjudication)
    )

    stranger = _republish_with_a_different_event(session, store, key="stranger")

    with pytest.raises(ValueError, match="no longer"):
        resolve_merge_review(
            session,
            decision=IdentityAdjudicationDecision.DISTINCT_EVENT,
            reviewer="test-human",
            rationale="Two different events that share 1964-03-27.",
        )
    assert (
        session.scalar(select(func.count()).select_from(EventIdentityAdjudication))
        == before
    )
    assert (
        adjudicated_distinct(
            session,
            event_a_id=_wikidata_event(session).id,
            event_b_id=stranger.id,
        )
        is False
    )


@pytest.mark.integration
def test_a_stale_active_task_is_retired_and_replaced_not_reused(
    session: Session, tmp_path: Path
) -> None:
    """Refusing a stale task must not strand the candidate.

    Reusing an active task whose collision has moved, while resolution refuses
    it as stale, is a publish/reject loop with no way out but editing the
    database. The stale task is retired and one for the current collision opened.
    """
    publish_golden(session, tmp_path)
    _prepare_for_publication(session, tmp_path)
    rebuild_coverage_index(session)
    session.flush()
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    first = publish_wikidata_event(session, store=store)
    assert first.merge_review_task_id is not None
    # Captured before the stranger exists: it mints its own identity claim, and
    # the test helper matches claim type across every release.
    identity = _claim(session, "candidate_event_identity")

    stranger = _republish_with_a_different_event(session, store, key="stranger")

    second = publish_wikidata_event(session, store=store)

    assert second.status == "deferred_to_merge_review"
    assert second.merge_review_task_id != first.merge_review_task_id
    stale = session.get(ReviewTask, first.merge_review_task_id)
    assert stale is not None
    assert stale.status == "dismissed"
    assert stale.completed_at is not None
    fresh = session.get(ReviewTask, second.merge_review_task_id)
    assert fresh is not None
    assert fresh.context_manifest_id == second.colliding_manifest_id
    # Exactly one active task, and the reviewer can now actually answer it.
    assert (
        session.scalar(
            select(func.count())
            .select_from(ReviewTask)
            .where(
                ReviewTask.claim_id == identity.id,
                ReviewTask.status.in_(("open", "in_progress")),
            )
        )
        == 1
    )
    recorded = resolve_merge_review(
        session,
        decision=IdentityAdjudicationDecision.DISTINCT_EVENT,
        reviewer="test-human",
        rationale="Distinct from the event that now holds the date.",
    )
    assert recorded
    assert (
        adjudicated_distinct(
            session,
            event_a_id=_wikidata_event(session).id,
            event_b_id=stranger.id,
        )
        is True
    )


@pytest.mark.integration
def test_a_merge_review_task_with_no_recorded_subject_is_refused(
    session: Session, tmp_path: Path
) -> None:
    """The other side of the staleness guard: absence is not permission.

    Every merge-review task this module opens records the collision it asked
    about. One without that binding cannot be shown to concern the pair being
    answered, so it fails closed rather than defaulting to the current
    collision.
    """
    publish_golden(session, tmp_path)
    _prepare_for_publication(session, tmp_path)
    rebuild_coverage_index(session)
    session.flush()
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    deferred = publish_wikidata_event(session, store=store)
    assert deferred.merge_review_task_id is not None

    task = session.get(ReviewTask, deferred.merge_review_task_id)
    assert task is not None
    assert task.context_manifest_id == deferred.colliding_manifest_id
    task.context_manifest_id = None
    session.flush()

    with pytest.raises(ValueError, match="no longer"):
        resolve_merge_review(
            session,
            decision=IdentityAdjudicationDecision.DISTINCT_EVENT,
            reviewer="test-human",
            rationale="A task with no recorded subject.",
        )


@pytest.mark.integration
def test_a_republication_of_the_same_event_still_allows_adjudication(
    session: Session, tmp_path: Path
) -> None:
    """Staleness is about the events, not the manifest version.

    Republishing the same recorded event mints a new manifest. Refusing on
    manifest identity alone would block a reviewer from ever answering a date
    that had been republished for an unrelated reason.
    """
    _, golden = publish_golden(session, tmp_path)
    _prepare_for_publication(session, tmp_path)
    rebuild_coverage_index(session)
    session.flush()
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    publish_wikidata_event(session, store=store)

    # Republish the date on the same recorded-event evidence: a new manifest
    # version, the same canonical event behind it.
    golden_manifest = session.get(
        PublicationManifest, golden.publication_manifest_id
    )
    assert golden_manifest is not None
    same_roots = list(
        session.scalars(
            select(PublicationStatementEvidence.resolved_claim_id)
            .where(
                PublicationStatementEvidence.publication_manifest_id
                == golden_manifest.id,
                PublicationStatementEvidence.statement_path.startswith(
                    "/sections/recorded_on_this_date/", autoescape=True
                ),
                PublicationStatementEvidence.resolved_claim_id.is_not(None),
            )
            .order_by(PublicationStatementEvidence.statement_path)
        )
    )
    assert same_roots
    # The whole recorded section is carried, as a real republication would: the
    # event resolves through the occurrence root, which is not the first one.
    assert events_behind_manifest(session, manifest=golden_manifest)
    publish_day_profile(
        session,
        store=store,
        profile_date=GOLDEN_DATE,
        profile_type=golden_manifest.profile_type,
        payload={
            "schema_version": "1",
            "date": GOLDEN_DATE.isoformat(),
            "profile_type": golden_manifest.profile_type.value,
            "sections": {
                "recorded_on_this_date": [
                    {
                        "statement_id": f"golden-republished-{index}",
                        "statement": "The same recorded event, republished.",
                        "details": {},
                        "provenance_note": "development fixture",
                    }
                    for index in range(len(same_roots))
                ]
            },
            "section_states": {"recorded_on_this_date": {"status": "available"}},
        },
        statement_evidence=[
            PublicationStatementEvidenceInput(
                statement_path=f"/sections/recorded_on_this_date/{index}",
                resolved_claim_id=root,
            )
            for index, root in enumerate(same_roots)
        ],
        recorded_events=[
            RecordedEventBinding(
                event_id=_usgs_event_id(session, golden_manifest),
                is_featured=True,
                featured_selection_id=None,
                statement_count=len(same_roots),
            )
        ],
        supersedes_manifest_id=golden_manifest.id,
        supersedes_day_profile_id=golden.id,
        editorial_revision=golden_manifest.editorial_revision + 1,
    )
    rebuild_coverage_index(session)
    session.flush()

    recorded = resolve_merge_review(
        session,
        decision=IdentityAdjudicationDecision.DISTINCT_EVENT,
        reviewer="test-human",
        rationale="Same two events; the date was merely republished.",
    )
    _feature_the_wikidata_event(session)
    session.flush()

    assert recorded
    assert publish_wikidata_event(session, store=store).status == "published"


@pytest.mark.integration
def test_a_collision_with_no_resolvable_event_does_not_bypass(
    session: Session, tmp_path: Path
) -> None:
    """Fail closed when the colliding manifest resolves to no canonical event.

    ``all()`` over an empty set is True, so an unresolvable collision would
    otherwise read as "every colliding event was adjudicated distinct" and bypass
    the guard exactly where the collision is least understood.

    The fixture also pins the section filter: this manifest *does* cite the
    occurrence root as evidence, but in the annual-context section, and a context
    statement is not a recorded event.
    """
    _prepare_for_publication(session, tmp_path)
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    context = _publish_prior_context(session, store)
    manifest = session.get(PublicationManifest, context.publication_manifest_id)
    assert manifest is not None

    assert events_behind_manifest(session, manifest=manifest) == set()
    assert _collision_adjudication(
        session, event=_wikidata_event(session), manifest=manifest
    ) == (False, None)


@pytest.mark.integration
def test_a_non_distinct_decision_blocks_without_reopening_the_task(
    session: Session, tmp_path: Path
) -> None:
    """A recorded answer is an answer, not a reason to ask again.

    Reopening the task on every retry is the duplicate-task treadmill the durable
    record exists to end.
    """
    publish_golden(session, tmp_path)
    _prepare_for_publication(session, tmp_path)
    rebuild_coverage_index(session)
    session.flush()
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    publish_wikidata_event(session, store=store)

    recorded = resolve_merge_review(
        session,
        decision=IdentityAdjudicationDecision.DEFERRED,
        reviewer="test-human",
        rationale="Not settled yet; needs more evidence.",
    )
    session.flush()

    outcome = publish_wikidata_event(session, store=store)

    assert outcome.status == "blocked_by_adjudication"
    assert outcome.adjudication_id == recorded[0].id
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
        == 0
    )
