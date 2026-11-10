"""Exact subjects across the real Wikidata workflow; all inputs stay offline."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import wikidata
from app.adapters.base import LocalFilesystemRawSourceStore
from app.governance import (
    EditorialSelection,
    EventIdentityAdjudication,
    IdentityAdjudicationDecision,
    ReviewDecisionValue,
    record_claim_review,
    record_editorial_selection,
)
from app.models import (
    Claim,
    ClaimAssertionStatus,
    Event,
    EventTime,
    PublicationManifest,
    PublicationStatementEvidence,
    ResolvedClaim,
    ResolvedClaimEvidence,
    ReviewTask,
    Source,
    SourceRelease,
)
from app.services import LocalFilesystemPublishedProfileStore

from .test_usgs_vertical_slice import publish as publish_usgs
from .test_wikidata_live_ingest import RecordingFetcher, entity_document

STAGES = ("enrich", "resolve", "adjudicate", "publish")


def _ingest(session: Session, tmp: Path, *, qid: str, day: str, revision: int = 1) -> UUID:
    payload = entity_document(
        entity_id=qid,
        revision_id=revision,
        occurrence=day,
        label=f"SYNTHETIC {qid} revision {revision}",
        optional_properties=False,
    )
    result = wikidata.ingest_wikidata_entity(
        session,
        entity_id=qid,
        revision_id=revision,
        fetcher=RecordingFetcher(payload, revision),
        raw_store=LocalFilesystemRawSourceStore(tmp / "raw"),
    )
    assert result.source_release_id is not None
    return result.source_release_id


def _review(session: Session, release: UUID) -> None:
    for claim in session.scalars(select(Claim).where(Claim.source_release_id == release)):
        record_claim_review(
            session,
            claim=claim,
            decision=ReviewDecisionValue.ACCEPTED,
            rationale="Offline test acceptance",
            reviewed_by="test-human",
        )


def _rank(session: Session, qid: str, day: date) -> None:
    for rank, predicate in enumerate(
        ("candidate_name", "candidate_event_type", "candidate_occurrence_date"), start=1
    ):
        resolved = session.scalar(
            select(ResolvedClaim)
            .where(ResolvedClaim.canonical_key == f"wikidata:{qid}:{predicate}")
            .order_by(ResolvedClaim.version.desc())
        )
        assert resolved is not None
        record_editorial_selection(
            session,
            profile_date=day,
            section_key="recorded_on_this_date",
            resolved_claim_id=resolved.id,
            display_rank=rank,
            rationale="Offline test selection",
            reviewed_by="test-human",
        )


def _run(stage: str, session: Session, tmp: Path, release: UUID | None = None) -> Any:
    kwargs: dict[str, Any] = {"source_release_id": release}
    if stage == "publish":
        kwargs["store"] = LocalFilesystemPublishedProfileStore(tmp / "published")
    if stage == "adjudicate":
        kwargs.update(
            decision=IdentityAdjudicationDecision.DISTINCT_EVENT,
            reviewer="test-human",
            rationale="Offline distinct-event test",
        )
    name = {
        "enrich": "attempt_wikidata_enrichment",
        "resolve": "resolve_wikidata_event",
        "adjudicate": "resolve_merge_review",
        "publish": "publish_wikidata_event",
    }[stage]
    return getattr(wikidata, name)(session, **kwargs)


def _counts(session: Session) -> dict[str, int]:
    return {
        model.__name__: session.scalar(select(func.count()).select_from(model)) or 0
        for model in (
            Event,
            EventTime,
            ResolvedClaim,
            ReviewTask,
            EditorialSelection,
            EventIdentityAdjudication,
            PublicationManifest,
        )
    }


@pytest.mark.integration
def test_first_entity_reachable_through_every_stage_after_second_ingest(
    session: Session, tmp_path: Path
) -> None:
    first = _ingest(session, tmp_path, qid="Q910000001", day="1964-03-27")
    second = _ingest(session, tmp_path, qid="Q910000002", day="1901-03-15")
    _review(session, first)
    _, golden = publish_usgs(session, tmp_path)
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    prior = session.get(PublicationManifest, golden.publication_manifest_id)
    assert prior is not None
    old_payload = store.read(prior.storage_uri, prior.content_hash)

    assert _run("enrich", session, tmp_path, first).occurrence_date == date(1964, 3, 27)
    event = _run("resolve", session, tmp_path, first)
    assert "Q910000001" in event.canonical_title
    _rank(session, "Q910000001", date(1964, 3, 27))
    deferred = _run("publish", session, tmp_path, first)
    assert deferred.status == "deferred_to_merge_review"
    decisions = _run("adjudicate", session, tmp_path, first)
    assert len(decisions) == 1
    assert event.id in {decisions[0].event_a_id, decisions[0].event_b_id}
    published = _run("publish", session, tmp_path, first)
    assert published.status == "published"
    assert _run("publish", session, tmp_path, first).manifest_id == published.manifest_id

    manifest = session.get(PublicationManifest, published.manifest_id)
    assert manifest is not None
    payload = LocalFilesystemPublishedProfileStore(tmp_path / "published").read(
        manifest.storage_uri, manifest.content_hash
    )
    old_text = {item["statement"] for item in old_payload["sections"]["recorded_on_this_date"]}
    new_text = {item["statement"] for item in payload["sections"]["recorded_on_this_date"]}
    assert old_text <= new_text
    assert any("Q910000001" in text for text in new_text)
    assert session.scalar(
        select(func.count())
        .select_from(PublicationStatementEvidence)
        .where(PublicationStatementEvidence.publication_manifest_id == manifest.id)
    ) == sum(len(items) for items in payload["sections"].values())
    second_claims = list(session.scalars(select(Claim).where(Claim.source_release_id == second)))
    assert second_claims
    assert all(claim.assertion_status is ClaimAssertionStatus.CANDIDATE for claim in second_claims)
    _review(session, second)
    _run("resolve", session, tmp_path, second)
    _rank(session, "Q910000002", date(1901, 3, 15))
    assert _run("publish", session, tmp_path, second).status == "published"


@pytest.mark.integration
@pytest.mark.parametrize("stage", STAGES)
def test_ambiguous_default_refuses_before_writes(
    session: Session, tmp_path: Path, stage: str
) -> None:
    first = _ingest(session, tmp_path, qid="Q910000001", day="1901-03-15")
    second = _ingest(session, tmp_path, qid="Q910000002", day="1902-03-15")
    before = _counts(session)
    with pytest.raises(ValueError, match="--release") as error:
        _run(stage, session, tmp_path)
    assert str(first) in str(error.value) and str(second) in str(error.value)
    assert _counts(session) == before


@pytest.mark.integration
def test_concurrent_revisions_never_mix_core_resolution_evidence(
    session: Session, tmp_path: Path
) -> None:
    releases = [
        _ingest(session, tmp_path, qid="Q910000001", day="1901-03-15", revision=1),
        _ingest(session, tmp_path, qid="Q910000001", day="1902-03-15", revision=2),
    ]
    for release in releases:
        _review(session, release)
    session.commit()
    engine = session.get_bind()

    def resolve(release: UUID) -> tuple[str, UUID]:
        with Session(bind=engine) as current:
            try:
                wikidata.resolve_wikidata_event(current, source_release_id=release)
                current.commit()
                return "resolved", release
            except ValueError as error:
                assert "resolution" in str(error) and "release" in str(error)
                current.rollback()
                return "refused", release

    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(resolve, releases))
    assert sorted(status for status, _ in outcomes) == ["refused", "resolved"]
    winner = next(release for status, release in outcomes if status == "resolved")
    assert session.scalar(select(func.count()).select_from(Event)) == 1
    core = [f"wikidata:Q910000001:{predicate}" for predicate in wikidata.REQUIRED_EVENT_CLAIMS]
    sources = set(
        session.scalars(
            select(Claim.source_release_id)
            .join(ResolvedClaimEvidence, ResolvedClaimEvidence.claim_id == Claim.id)
            .join(ResolvedClaim, ResolvedClaim.id == ResolvedClaimEvidence.resolved_claim_id)
            .where(ResolvedClaim.canonical_key.in_(core))
        )
    )
    assert sources == {winner}
    assert session.scalar(
        select(func.count()).select_from(ResolvedClaim).where(ResolvedClaim.canonical_key.in_(core))
    ) == len(core)


@pytest.mark.integration
@pytest.mark.parametrize("stage", STAGES)
def test_missing_unknown_and_foreign_subjects_refuse(
    session: Session, tmp_path: Path, stage: str
) -> None:
    for release in (None, uuid4()):
        before = _counts(session)
        with pytest.raises(ValueError):
            _run(stage, session, tmp_path, release)
        assert _counts(session) == before
    publish_usgs(session, tmp_path)
    foreign = session.scalar(
        select(SourceRelease.id).join(Source).where(Source.slug != wikidata.WIKIDATA_SOURCE_SLUG)
    )
    assert foreign is not None
    before = _counts(session)
    with pytest.raises(ValueError):
        _run(stage, session, tmp_path, foreign)
    assert _counts(session) == before


@pytest.mark.integration
@pytest.mark.parametrize("stage", ("resolve", "adjudicate", "publish"))
def test_same_entity_new_revision_cannot_reuse_old_core(
    session: Session, tmp_path: Path, stage: str
) -> None:
    original = _ingest(session, tmp_path, qid="Q910000001", day="1901-03-15")
    _review(session, original)
    # Singleton compatibility remains usable.
    wikidata.resolve_wikidata_event(session)
    _rank(session, "Q910000001", date(1901, 3, 15))
    published = _run("publish", session, tmp_path, original)
    newer = _ingest(session, tmp_path, qid="Q910000001", day="1902-03-15", revision=2)
    _review(session, newer)
    before = _counts(session)
    with pytest.raises(ValueError, match="resolution.*release|release.*resolution"):
        _run(stage, session, tmp_path, newer)
    assert _counts(session) == before
    assert _run("publish", session, tmp_path, original).manifest_id == published.manifest_id


@pytest.mark.integration
def test_existing_occurrence_must_still_agree_with_selected_release(
    session: Session, tmp_path: Path
) -> None:
    release = _ingest(session, tmp_path, qid="Q910000001", day="1901-03-15")
    _review(session, release)
    event = _run("resolve", session, tmp_path, release)
    time = session.scalars(select(EventTime).where(EventTime.event_id == event.id)).one()
    time.start_date += timedelta(days=1)
    session.flush()
    before = _counts(session)
    with pytest.raises(ValueError, match="occurrence"):
        _run("resolve", session, tmp_path, release)
    with pytest.raises(ValueError, match="occurrence"):
        _run("publish", session, tmp_path, release)
    assert _counts(session) == before
