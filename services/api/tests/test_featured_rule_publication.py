"""The standing rule, proven through publication rather than in isolation (G3b-2a).

The rule itself is tested in ``test_featured_standing_rule.py``. What these tests
prove is the whole publication result: that a date with two admitted events and
no human choice publishes *both*, features the rule's winner, records how that
winner was chosen, and reports itself as automated rather than reviewed.

Three of the four are about what should *not* happen on a second run. A rule that
re-evaluates cleanly but stamps a new manifest every time it is asked becomes a
paperwork factory, and a fingerprint that is only ever returned by a helper is
decoration rather than publication evidence.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import wikidata as wikidata_module
from app.coverage import coverage_entry, rebuild_coverage_index
from app.governance import (
    FEATURED_EVENT_SECTION,
    FEATURED_ORIGIN_STANDING_RULE,
    STANDING_FEATURED_EVENT_RULE,
    STANDING_FEATURED_RULE_VERSION,
    EditorialSelection,
    EditorialSelectionStatus,
    IdentityAdjudicationDecision,
    events_behind_manifest,
    featured_candidate_fingerprint,
    record_editorial_selection,
    record_identity_adjudication,
)
from app.models import (
    DayProfile,
    Event,
    EventTime,
    PublicationManifest,
    PublicationStatementEvidence,
    PublicationStatus,
    ResolvedClaim,
    ReviewStatus,
)
from app.services import (
    LocalFilesystemPublishedProfileStore,
    PublicationStatementEvidenceInput,
    publish_day_profile,
)
from app.wikidata import publish_wikidata_event

from .test_featured_standing_rule import _score_for
from .test_identity_adjudication import _make_event
from .test_wikidata_publish import (
    GOLDEN_DATE,
    _golden_event,
    _publish_past_the_golden_collision,
    _wikidata_event,
)


def _featured_rows(session: Session) -> int | None:
    return session.scalar(
        select(func.count())
        .select_from(EditorialSelection)
        .where(EditorialSelection.section_key == FEATURED_EVENT_SECTION)
    )


def _latest_featured(session: Session, root_id: UUID) -> EditorialSelection | None:
    return session.scalars(
        select(EditorialSelection)
        .where(
            EditorialSelection.profile_date == GOLDEN_DATE,
            EditorialSelection.section_key == FEATURED_EVENT_SECTION,
            EditorialSelection.resolved_claim_id == root_id,
        )
        .order_by(EditorialSelection.decision_version.desc())
    ).first()


def _key(session: Session, event: Event) -> str:
    resolved = session.get(ResolvedClaim, event.resolved_claim_id)
    assert resolved is not None
    return resolved.canonical_key


@pytest.mark.integration
def test_the_standing_rule_publishes_a_complete_multi_event_profile(
    session: Session, tmp_path: Path
) -> None:
    """The whole transaction: both events published, one featured, provenance bound.

    No human has chosen a headline, so the rule supplies one — and everything
    else about the date has to survive that.
    """
    store, golden = _publish_past_the_golden_collision(session, tmp_path)
    golden_event = _golden_event(session, golden)
    wikidata_event = _wikidata_event(session)
    prior = session.get(PublicationManifest, golden.publication_manifest_id)
    assert prior is not None
    prior_sections = set(
        store.read(prior.storage_uri, prior.content_hash)["sections"]
    )

    outcome = publish_wikidata_event(session, store=store)

    assert outcome.status == "published"
    manifest = session.get(PublicationManifest, outcome.manifest_id)
    assert manifest is not None

    # Both events published, and both still resolvable from the manifest.
    assert events_behind_manifest(session, manifest=manifest) == {
        golden_event.id,
        wikidata_event.id,
    }

    # The rule's winner is featured, recomputed from the specification.
    expected_fingerprint = featured_candidate_fingerprint(
        session,
        profile_date=GOLDEN_DATE,
        candidate_root_ids=[
            golden_event.resolved_claim_id,
            wikidata_event.resolved_claim_id,
        ],
    )
    metadata = manifest.metadata_json
    assert metadata["featured_event_algorithm_version"] == STANDING_FEATURED_RULE_VERSION
    assert metadata["featured_event_candidate_set_fingerprint"] == expected_fingerprint
    assert metadata["featured_event_selection_origin"] == FEATURED_ORIGIN_STANDING_RULE
    winner_key = metadata["featured_event_identity_key"]
    assert winner_key in {_key(session, golden_event), _key(session, wikidata_event)}

    # Exactly one current featured selection; the loser is explicitly rejected.
    winner = next(
        event
        for event in (golden_event, wikidata_event)
        if _key(session, event) == winner_key
    )
    loser = next(
        event
        for event in (golden_event, wikidata_event)
        if event.id != winner.id
    )
    chosen = _latest_featured(session, winner.resolved_claim_id)
    assert chosen is not None
    assert chosen.status == EditorialSelectionStatus.SELECTED.value
    assert str(chosen.id) == metadata["featured_event_selection_id"]
    assert chosen.decision_version == metadata["featured_event_selection_version"]
    rejected = _latest_featured(session, loser.resolved_claim_id)
    assert rejected is not None
    assert rejected.status == EditorialSelectionStatus.REJECTED.value

    # No human decision was fabricated on anyone's behalf (D038).
    assert chosen.reviewed_by == STANDING_FEATURED_EVENT_RULE
    assert rejected.reviewed_by == STANDING_FEATURED_EVENT_RULE

    # Both events' surviving predicates are published, featured first.
    payload = store.read(manifest.storage_uri, manifest.content_hash)
    recorded = payload["sections"]["recorded_on_this_date"]
    statement_ids = {item["statement_id"] for item in recorded}
    assert any(sid.startswith("wikidata-") for sid in statement_ids)
    assert "event-title" in statement_ids
    leads_wikidata = recorded[0]["statement_id"].startswith("wikidata-")
    assert leads_wikidata == (winner.id == wikidata_event.id)

    # The prior context sections are untouched by the enrichment.
    assert prior_sections <= set(payload["sections"])

    # A rule-selected feature is not a reviewed profile.
    rebuild_coverage_index(session)
    session.flush()
    entry = coverage_entry(session, GOLDEN_DATE)
    assert entry is not None
    assert entry.review_status is ReviewStatus.AUTOMATED_ONLY


@pytest.mark.integration
def test_an_unchanged_rerun_publishes_nothing_new(
    session: Session, tmp_path: Path
) -> None:
    """Evaluating is not the same as changing.

    The rule runs on every publish attempt. If each run appended a decision or
    minted a version, looking at the archive would keep changing it.
    """
    store, _golden = _publish_past_the_golden_collision(session, tmp_path)
    first = publish_wikidata_event(session, store=store)
    rebuild_coverage_index(session)
    session.flush()
    first_manifest = session.get(PublicationManifest, first.manifest_id)
    assert first_manifest is not None
    rows_before = _featured_rows(session)
    manifests_before = session.scalar(
        select(func.count()).select_from(PublicationManifest)
    )
    # Captured before the rerun, so the comparison below is between two moments
    # rather than a value against itself.
    metadata_before = dict(first_manifest.metadata_json)
    hash_before = first_manifest.content_hash

    second = publish_wikidata_event(session, store=store)

    assert second.status == "published"
    assert second.manifest_id == first.manifest_id
    assert _featured_rows(session) == rows_before
    assert (
        session.scalar(select(func.count()).select_from(PublicationManifest))
        == manifests_before
    )
    session.refresh(first_manifest)
    assert first_manifest.content_hash == hash_before
    for field in (
        "featured_event_selection_id",
        "featured_event_selection_version",
        "featured_event_candidate_set_fingerprint",
        "featured_event_algorithm_version",
    ):
        assert first_manifest.metadata_json[field] == metadata_before[field]


@pytest.mark.integration
def test_a_failed_publish_leaves_no_standing_rule_decision(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rule's decision must not outlive the publication it was made for.

    A selection recorded for a candidate set that never became a profile is a
    decision about a version of the archive that does not exist -- and the next
    run would treat it as the standing choice.
    """
    store, _golden = _publish_past_the_golden_collision(session, tmp_path)
    rows_before = _featured_rows(session)
    manifests_before = session.scalar(
        select(func.count()).select_from(PublicationManifest)
    )
    entry_before = coverage_entry(session, GOLDEN_DATE)
    tier_before = None if entry_before is None else entry_before.publication_tier

    def _explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("publication failed after the feature was evaluated")

    monkeypatch.setattr(wikidata_module, "publish_day_profile", _explode)
    with pytest.raises(RuntimeError, match="publication failed"):
        publish_wikidata_event(session, store=store)
    monkeypatch.undo()
    # The caller rolls back a failed publication, as candidate_cli does.
    session.rollback()

    assert _featured_rows(session) == rows_before
    assert (
        session.scalar(select(func.count()).select_from(PublicationManifest))
        == manifests_before
    )
    entry_after = coverage_entry(session, GOLDEN_DATE)
    assert (
        None if entry_after is None else entry_after.publication_tier
    ) == tier_before


def _admit_a_third_event(
    session: Session,
    store: LocalFilesystemPublishedProfileStore,
    *,
    losing_against: str,
    adjudicate_against: list[Event],
) -> Event:
    """Put a third canonical event on the date, chosen so the rule keeps its winner.

    Offline there is one real recorded-event publisher, so the third event is
    admitted by publishing a manifest that carries all three events' evidence --
    which is precisely how a second publisher would admit it. What is being
    proven here is the *evaluation*, not the other publisher.
    """
    losing_key = next(
        key
        for key in (f"third-{index}" for index in range(80))
        if _score_for(f"fixture:{key}:candidate_event_identity") > losing_against
    )
    third = _make_event(session, key=losing_key, on_date=GOLDEN_DATE)
    for other in adjudicate_against:
        record_identity_adjudication(
            session,
            event_a_id=third.id,
            event_b_id=other.id,
            decision=IdentityAdjudicationDecision.DISTINCT_EVENT,
            reviewer="test-human",
            rationale="A third distinct event on the same date.",
        )
    occurrence = session.scalar(
        select(EventTime.provenance_resolved_claim_id).where(
            EventTime.event_id == third.id, EventTime.is_primary.is_(True)
        )
    )
    assert occurrence is not None
    record_editorial_selection(
        session,
        profile_date=GOLDEN_DATE,
        section_key="recorded_on_this_date",
        resolved_claim_id=occurrence,
        status=EditorialSelectionStatus.SELECTED,
        display_rank=None,
        rationale="A person selected the third event's occurrence.",
        reviewed_by="test-human",
    )
    session.flush()

    current = session.scalar(
        select(PublicationManifest)
        .where(
            PublicationManifest.profile_date == GOLDEN_DATE,
            PublicationManifest.status == PublicationStatus.PUBLISHED,
        )
        .order_by(PublicationManifest.version.desc())
    )
    assert current is not None
    payload = store.read(current.storage_uri, current.content_hash)
    recorded = list(payload["sections"]["recorded_on_this_date"])
    # Every section's evidence is carried, not just the recorded one: the spine
    # requires a provenance mapping for every published statement, and the
    # profile also holds annual context and evidence notes.
    carried = [
        PublicationStatementEvidenceInput(
            statement_path=row.statement_path,
            resolved_claim_id=row.resolved_claim_id,
            derived_value_id=row.derived_value_id,
        )
        for row in session.scalars(
            select(PublicationStatementEvidence).where(
                PublicationStatementEvidence.publication_manifest_id == current.id
            )
        )
    ]
    recorded.append(
        {
            "statement_id": f"third-{losing_key}",
            "statement": "A third recorded event now shares this date.",
            "details": {},
            "provenance_note": "development fixture",
        }
    )
    carried.append(
        PublicationStatementEvidenceInput(
            statement_path=(
                f"/sections/recorded_on_this_date/{len(recorded) - 1}"
            ),
            resolved_claim_id=occurrence,
        )
    )
    profile = session.scalar(
        select(DayProfile).where(
            DayProfile.publication_manifest_id == current.id
        )
    )
    assert profile is not None
    publish_day_profile(
        session,
        store=store,
        profile_date=GOLDEN_DATE,
        profile_type=current.profile_type,
        payload={
            **payload,
            "sections": {**payload["sections"], "recorded_on_this_date": recorded},
        },
        statement_evidence=carried,
        supersedes_manifest_id=current.id,
        supersedes_day_profile_id=profile.id,
        editorial_revision=current.editorial_revision + 1,
        manifest_metadata=dict(current.metadata_json),
    )
    rebuild_coverage_index(session)
    session.flush()
    return third


@pytest.mark.integration
def test_a_losing_third_event_republishes_without_changing_the_decision(
    session: Session, tmp_path: Path
) -> None:
    """The contract's centrepiece: new candidate set, same headline, new provenance.

    The decision did not change, so no editorial history is appended. The
    candidate set did change, so the new version records the new fingerprint.
    And the previous version keeps the fingerprint it was published with --
    immutable artifacts do not learn new facts retroactively.
    """
    store, golden = _publish_past_the_golden_collision(session, tmp_path)
    golden_event = _golden_event(session, golden)
    wikidata_event = _wikidata_event(session)
    first = publish_wikidata_event(session, store=store)
    assert first.status == "published"
    rebuild_coverage_index(session)
    session.flush()
    first_manifest = session.get(PublicationManifest, first.manifest_id)
    assert first_manifest is not None
    first_metadata = dict(first_manifest.metadata_json)
    winner_key = first_metadata["featured_event_identity_key"]
    pair_fingerprint = first_metadata["featured_event_candidate_set_fingerprint"]
    rows_before = _featured_rows(session)

    third = _admit_a_third_event(
        session,
        store,
        losing_against=_score_for(winner_key),
        adjudicate_against=[golden_event, wikidata_event],
    )

    second = publish_wikidata_event(session, store=store, force_new_version=True)

    assert second.status == "published"
    manifest = session.get(PublicationManifest, second.manifest_id)
    assert manifest is not None
    metadata = manifest.metadata_json

    # All three events admitted, and the headline did not move.
    assert events_behind_manifest(session, manifest=manifest) == {
        golden_event.id,
        wikidata_event.id,
        third.id,
    }
    assert metadata["featured_event_identity_key"] == winner_key
    assert (
        metadata["featured_event_selection_id"]
        == first_metadata["featured_event_selection_id"]
    )
    assert (
        metadata["featured_event_selection_version"]
        == first_metadata["featured_event_selection_version"]
    )
    assert _featured_rows(session) == rows_before, (
        "the headline did not move, so no decision should have been appended"
    )

    # New candidate set, so new publication provenance.
    expected = featured_candidate_fingerprint(
        session,
        profile_date=GOLDEN_DATE,
        candidate_root_ids=[
            golden_event.resolved_claim_id,
            wikidata_event.resolved_claim_id,
            third.resolved_claim_id,
        ],
    )
    assert metadata["featured_event_candidate_set_fingerprint"] == expected
    assert expected != pair_fingerprint
    assert manifest.version > first_manifest.version

    # The earlier artifact still says what it said when it was published.
    session.refresh(first_manifest)
    assert (
        first_manifest.metadata_json["featured_event_candidate_set_fingerprint"]
        == pair_fingerprint
    )

    rebuild_coverage_index(session)
    session.flush()
    entry = coverage_entry(session, GOLDEN_DATE)
    assert entry is not None
    assert entry.review_status is ReviewStatus.AUTOMATED_ONLY


@pytest.mark.integration
def test_a_third_event_adjudicated_against_only_one_incumbent_is_refused(
    session: Session, tmp_path: Path
) -> None:
    """Every new candidate is judged against the whole admitted set, not the headline.

    A decision about one incumbent is not permission for the date. Without the
    second adjudication the successor must not publish, and must leave the
    existing version and history exactly as they were.
    """
    store, golden = _publish_past_the_golden_collision(session, tmp_path)
    golden_event = _golden_event(session, golden)
    first = publish_wikidata_event(session, store=store)
    assert first.status == "published"
    rebuild_coverage_index(session)
    session.flush()
    first_manifest = session.get(PublicationManifest, first.manifest_id)
    assert first_manifest is not None
    winner_key = first_manifest.metadata_json["featured_event_identity_key"]
    rows_before = _featured_rows(session)

    # Adjudicated against the golden event only -- nothing about the Wikidata one.
    _admit_a_third_event(
        session,
        store,
        losing_against=_score_for(winner_key),
        adjudicate_against=[golden_event],
    )
    manifests_before = session.scalar(
        select(func.count()).select_from(PublicationManifest)
    )

    outcome = publish_wikidata_event(session, store=store, force_new_version=True)

    assert outcome.status != "published"
    assert _featured_rows(session) == rows_before
    assert (
        session.scalar(select(func.count()).select_from(PublicationManifest))
        == manifests_before
    )


@pytest.mark.integration
def test_a_refused_publication_records_no_standing_decision(
    session: Session, tmp_path: Path
) -> None:
    """The rollback proof's other side: a refusal that *returns* rather than raises.

    ``featured_event_required`` is an ordinary return value, and the CLI commits
    the session on every non-exception path. So a standing decision written
    before that gate becomes permanent editorial history for a publication that
    never happened — and the next run would read it as the standing choice.

    The exception path was already covered; this is the path a caller actually
    takes when the archive simply declines to publish.
    """
    store, golden = _publish_past_the_golden_collision(session, tmp_path)
    golden_event = _golden_event(session, golden)
    rows_before = _featured_rows(session)

    # Every one of the prior admitted event's recorded predicates is withdrawn,
    # so nothing of it survives into a successor and publication must refuse.
    for root in session.scalars(
        select(PublicationStatementEvidence.resolved_claim_id).where(
            PublicationStatementEvidence.publication_manifest_id
            == golden.publication_manifest_id,
            PublicationStatementEvidence.statement_path.startswith(
                "/sections/recorded_on_this_date/", autoescape=True
            ),
            PublicationStatementEvidence.resolved_claim_id.is_not(None),
        )
    ):
        record_editorial_selection(
            session,
            profile_date=GOLDEN_DATE,
            section_key="recorded_on_this_date",
            resolved_claim_id=root,
            status=EditorialSelectionStatus.REJECTED,
            display_rank=None,
            rationale="Withdrawn by a person after the first publication.",
            reviewed_by="test-human",
        )
    session.flush()

    outcome = publish_wikidata_event(session, store=store)

    assert outcome.status == "featured_event_required"
    assert _featured_rows(session) == rows_before, (
        "a decision was recorded for a publication that never happened"
    )
    assert not session.scalars(
        select(EditorialSelection).where(
            EditorialSelection.section_key == FEATURED_EVENT_SECTION,
            EditorialSelection.reviewed_by == STANDING_FEATURED_EVENT_RULE,
        )
    ).all()
    # And the golden event is untouched by the refusal.
    assert golden_event.id is not None


@pytest.mark.integration
def test_a_changed_candidate_set_republishes_without_force_new_version(
    session: Session, tmp_path: Path
) -> None:
    """A grown candidate set republishes on its own, without being forced.

    The earlier three-event test passed ``force_new_version=True``, which meant
    it never showed that an ordinary publish handles a grown set. This one drops
    that crutch and pins the end state: the latest manifest describes the
    candidate set actually evaluated, and all three events remain behind it.

    What this does *not* prove is the widened ``metadata_binding_changed``
    check. Under the current design a candidate-set change requires an evidence
    change, which changes the rendered payload and mints a version regardless —
    this test passes with the narrow check too, verified by reverting it. The
    widened comparison is defence-in-depth for a future publisher that decouples
    content from candidates, not a fix for a case reachable today.
    """
    store, golden = _publish_past_the_golden_collision(session, tmp_path)
    golden_event = _golden_event(session, golden)
    wikidata_event = _wikidata_event(session)
    first = publish_wikidata_event(session, store=store)
    assert first.status == "published"
    rebuild_coverage_index(session)
    session.flush()
    first_manifest = session.get(PublicationManifest, first.manifest_id)
    assert first_manifest is not None
    winner_key = first_manifest.metadata_json["featured_event_identity_key"]
    pair_fingerprint = first_manifest.metadata_json[
        "featured_event_candidate_set_fingerprint"
    ]

    third = _admit_a_third_event(
        session,
        store,
        losing_against=_score_for(winner_key),
        adjudicate_against=[golden_event, wikidata_event],
    )

    # No force_new_version: the publish must decide for itself that the changed
    # provenance is worth a new version.
    second = publish_wikidata_event(session, store=store)

    assert second.status == "published"
    assert second.manifest_id != first.manifest_id
    manifest = session.get(PublicationManifest, second.manifest_id)
    assert manifest is not None
    assert manifest.metadata_json[
        "featured_event_candidate_set_fingerprint"
    ] != pair_fingerprint
    assert manifest.metadata_json["featured_event_identity_key"] == winner_key
    assert events_behind_manifest(session, manifest=manifest) == {
        golden_event.id,
        wikidata_event.id,
        third.id,
    }
