"""Coverage index (epic #32, slice AA3).

Once every 1950-2025 date carries annual context, "is anything published?"
stops being a useful question. The index answers the questions that replace
it: how rich is this date, does it hold a recorded event, which strata have
content, and where is the nearest date worth travelling to.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.base import LocalFilesystemRawSourceStore
from app.batch_publication import CONTEXT_BATCH_KIND, run_context_batch, start_batch_run
from app.coverage import (
    CoverageRecord,
    coverage_for_date,
    coverage_summary,
    rebuild_coverage_index,
)
from app.models import CoverageEntry, PublicationManifest, PublicationTier
from app.services import (
    LocalFilesystemPublishedProfileStore,
    PublicationStatementEvidenceInput,
    create_claim,
    publish_day_profile,
    resolve_claim,
)
from app.un_wpp import ingest_un_wpp, review_un_wpp

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "fixtures"
    / "un-wpp"
    / "wpp2024-world-1950-2025.csv"
)


@pytest.fixture()
def reviewed_un_wpp(session: Session, tmp_path: Path) -> None:
    result = ingest_un_wpp(
        session,
        fixture_path=FIXTURE,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )
    assert result.source_release_id is not None
    review_un_wpp(session, result.source_release_id)
    session.commit()


def delete_coverage_entries() -> Any:
    """Simulate an archive that predates the index (or a dropped table)."""
    from sqlalchemy import delete

    return delete(CoverageEntry)


def _synthetic_release(session: Session, label: str) -> Any:
    """A dedicated single-record source per enriched date.

    The shared helper uses a fixed slug (so it cannot be called twice), and
    reusing the UN WPP release would demand per-claim source-record hashes.
    """
    from app.models import LegalReviewStatus, Source
    from app.services import create_source_release

    source = Source(
        slug=f"test-source-{label}",
        name=f"Synthetic source for {label}",
        publisher="Test suite",
        canonical_url=f"https://example.invalid/{label}",
        legal_review_status=LegalReviewStatus.NOT_REQUIRED,
    )
    session.add(source)
    session.flush()
    return create_source_release(
        session,
        source_id=source.id,
        release_label=f"{label}-v1",
        source_url=f"https://example.invalid/{label}/v1",
        raw_storage_uri=f"test://raw/{label}-v1",
        raw_bytes=f"raw bytes for {label}".encode(),
        raw_record_count=1,
    )


def publish_enriched(
    session: Session,
    store: LocalFilesystemPublishedProfileStore,
    profile_date: date,
    *,
    label: str,
) -> None:
    from app.models import ProfileType

    release = _synthetic_release(session, label)
    claim = create_claim(
        session,
        source_release_id=release.id,
        source_record_locator=f"record:{label}",
        claim_type="synthetic_assertion",
        assertion_text=f"Recorded event for {label}.",
    )
    resolved = resolve_claim(
        session,
        canonical_key=f"test:{label}",
        resolved_value={"statement": "A recorded event."},
        rationale="Test-only recorded event.",
        supporting_claim_ids=[claim.id],
    )
    publish_day_profile(
        session,
        store=store,
        profile_date=profile_date,
        profile_type=ProfileType.STANDARD_STATISTICAL,
        payload={
            "schema_version": "1",
            "date": profile_date.isoformat(),
            "profile_type": "standard_statistical",
            "sections": {
                "recorded_on_this_date": [
                    {
                        "statement_id": "event",
                        "statement": "A recorded event.",
                        "details": {"quality_grade": "B"},
                    }
                ]
            },
            "quality": {"grade": "B", "explanation": "Single validated source."},
        },
        statement_evidence=[
            PublicationStatementEvidenceInput(
                statement_path="/sections/recorded_on_this_date/0",
                resolved_claim_id=resolved.id,
            )
        ],
    )


@pytest.mark.integration
def test_the_index_records_richness_not_merely_publication(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    context_date = date(1980, 5, 5)
    enriched_date = date(1980, 5, 7)
    run = start_batch_run(
        session,
        kind=CONTEXT_BATCH_KIND,
        requested={"dates": [context_date.isoformat()]},
    )
    run_context_batch(session, store=store, dates=[context_date], batch_run=run)
    publish_enriched(session, store, enriched_date, label="index-enriched")
    session.commit()

    rebuild_coverage_index(session)
    session.commit()

    context = coverage_for_date(session, context_date)
    assert context is not None
    assert context.publication_tier is PublicationTier.CONTEXT_ONLY
    assert context.sections["recorded_on_this_date"] == 0
    assert context.sections["typical_day_in_this_year"] == 2
    assert context.sections["wider_historical_context"] == 3
    assert context.has_recorded_event is False

    enriched = coverage_for_date(session, enriched_date)
    assert enriched is not None
    assert enriched.publication_tier is PublicationTier.REVIEWED_ENRICHED
    assert enriched.sections["recorded_on_this_date"] == 1
    assert enriched.has_recorded_event is True
    assert enriched.quality_floor == "B"


@pytest.mark.integration
def test_unpublished_dates_are_absent_rather_than_reported_empty(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    """The index must never imply a profile exists for a date that has none."""
    rebuild_coverage_index(session)
    session.commit()
    assert coverage_for_date(session, date(1955, 1, 1)) is None


@pytest.mark.integration
def test_nearest_enriched_skips_the_sea_of_context_profiles(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    """Stepping day by day through near-identical context pages is exactly
    what the index exists to prevent."""
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    context_dates = [date(1981, 3, day) for day in range(1, 11)]
    run = start_batch_run(
        session,
        kind=CONTEXT_BATCH_KIND,
        requested={"dates": [value.isoformat() for value in context_dates]},
    )
    run_context_batch(session, store=store, dates=context_dates, batch_run=run)
    publish_enriched(session, store, date(1981, 3, 20), label="nearest-after")
    publish_enriched(session, store, date(1981, 2, 10), label="nearest-before")
    session.commit()
    rebuild_coverage_index(session)
    session.commit()

    record = coverage_for_date(session, date(1981, 3, 5))
    assert record is not None
    assert record.nearest_enriched_after == date(1981, 3, 20)
    assert record.nearest_enriched_before == date(1981, 2, 10)
    assert record.nearest_recorded_event_after == date(1981, 3, 20)


@pytest.mark.integration
def test_the_summary_reports_the_shape_of_the_archive(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    dates = [date(1982, 6, day) for day in range(1, 5)]
    run = start_batch_run(
        session,
        kind=CONTEXT_BATCH_KIND,
        requested={"dates": [value.isoformat() for value in dates]},
    )
    run_context_batch(session, store=store, dates=dates, batch_run=run)
    publish_enriched(session, store, date(1982, 7, 1), label="summary-enriched")
    session.commit()
    rebuild_coverage_index(session)
    session.commit()

    summary = coverage_summary(session)
    assert summary.total_published == 5
    assert summary.by_tier["context_only"] == 4
    assert summary.by_tier["reviewed_enriched"] == 1
    assert summary.with_recorded_event == 1
    assert summary.earliest == date(1982, 6, 1)
    assert summary.latest == date(1982, 7, 1)
    assert summary.index_version >= 1


@pytest.mark.integration
def test_rebuilding_is_idempotent_and_follows_supersession(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    """A regenerated index must describe the archive as it is now, including
    after a correction, and must not accumulate duplicate rows."""
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    profile_date = date(1983, 4, 4)
    run = start_batch_run(
        session,
        kind=CONTEXT_BATCH_KIND,
        requested={"dates": [profile_date.isoformat()]},
    )
    run_context_batch(session, store=store, dates=[profile_date], batch_run=run)
    session.commit()

    rebuild_coverage_index(session)
    session.commit()
    first = coverage_for_date(session, profile_date)
    assert first is not None and first.publication_tier is PublicationTier.CONTEXT_ONLY

    rebuild_coverage_index(session)
    session.commit()
    assert (
        len(
            list(
                session.scalars(
                    select(CoverageEntry).where(
                        CoverageEntry.profile_date == profile_date
                    )
                )
            )
        )
        == 1
    )

    publish_enriched(session, store, profile_date, label="correction")
    session.commit()
    rebuild_coverage_index(session)
    session.commit()
    corrected = coverage_for_date(session, profile_date)
    assert corrected is not None
    assert corrected.publication_tier is PublicationTier.REVIEWED_ENRICHED


@pytest.mark.integration
def test_publication_updates_coverage_without_a_full_rebuild(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    """Coverage is maintained as the last step of publication, so the index
    is never stale between bulk runs."""
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    profile_date = date(1984, 8, 8)
    run = start_batch_run(
        session,
        kind=CONTEXT_BATCH_KIND,
        requested={"dates": [profile_date.isoformat()]},
    )
    run_context_batch(session, store=store, dates=[profile_date], batch_run=run)
    session.commit()

    record = coverage_for_date(session, profile_date)
    assert isinstance(record, CoverageRecord)
    assert record.publication_tier is PublicationTier.CONTEXT_ONLY


@pytest.mark.integration
def test_the_coverage_api_serves_richness_and_neighbours(
    session: Session, tmp_path: Path, reviewed_un_wpp: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from app import main
    from app.database import get_session

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    context_dates = [date(1985, 1, day) for day in range(1, 4)]
    run = start_batch_run(
        session,
        kind=CONTEXT_BATCH_KIND,
        requested={"dates": [value.isoformat() for value in context_dates]},
    )
    run_context_batch(session, store=store, dates=context_dates, batch_run=run)
    publish_enriched(session, store, date(1985, 2, 14), label="api-enriched")
    session.commit()
    rebuild_coverage_index(session, store=store)
    session.commit()

    def override_session() -> object:
        yield session

    main.app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(main.app)
        summary = client.get("/api/v1/coverage")
        detail = client.get("/api/v1/coverage/1985-01-02")
        unindexed = client.get("/api/v1/coverage/1999-01-01")
        out_of_range = client.get("/api/v1/coverage/1899-01-01")
    finally:
        main.app.dependency_overrides.clear()

    assert summary.status_code == 200
    body = summary.json()
    assert body["total_published"] == 4
    assert body["by_tier"]["context_only"] == 3
    assert body["by_tier"]["reviewed_enriched"] == 1
    assert body["with_recorded_event"] == 1
    assert body["supported_range"] == {
        "minimum": "1900-01-01",
        "maximum": "2025-12-31",
    }

    assert detail.status_code == 200
    record = detail.json()
    assert record["publication_tier"] == "context_only"
    assert record["has_recorded_event"] is False
    assert record["sections"]["typical_day_in_this_year"] == 2
    assert record["nearest_enriched_after"] == "1985-02-14"
    assert record["nearest_recorded_event_after"] == "1985-02-14"

    assert unindexed.status_code == 404
    assert unindexed.json()["status"] == "coverage_not_indexed"
    assert out_of_range.status_code == 404
    assert out_of_range.json()["status"] == "date_out_of_supported_range"


# --- Round 1 review findings (PR #43) ------------------------------------


@pytest.mark.integration
def test_reconcile_repair_indexes_the_profile_it_completes(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repaired publication is served by /api/v1/day, so coverage must not
    keep reporting it missing until someone runs a full rebuild."""
    from app.models import ProfileType
    from app.services import reconcile_publications

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    profile_date = date(1975, 4, 30)
    claim = create_claim(
        session,
        source_release_id=_synthetic_release(session, "repair-coverage").id,
        source_record_locator="record:repair",
        claim_type="synthetic_assertion",
        assertion_text="A recorded event.",
    )
    resolved = resolve_claim(
        session,
        canonical_key="test:repair-coverage",
        resolved_value={"statement": "A recorded event."},
        rationale="Test-only recorded event.",
        supporting_claim_ids=[claim.id],
    )
    evidence = [
        PublicationStatementEvidenceInput(
            statement_path="/sections/recorded_on_this_date/0",
            resolved_claim_id=resolved.id,
        )
    ]

    from app import services as services_module

    def explode(*args: object, **inner: object) -> None:
        raise RuntimeError("Simulated crash before artifact promotion.")

    monkeypatch.setattr(services_module.StagedProfileWrite, "finalize", explode)
    with pytest.raises(RuntimeError, match="Simulated crash"):
        publish_day_profile(
            session,
            store=store,
            profile_date=profile_date,
            profile_type=ProfileType.STANDARD_STATISTICAL,
            payload={
                "schema_version": "1",
                "date": profile_date.isoformat(),
                "profile_type": "standard_statistical",
                "sections": {
                    "recorded_on_this_date": [
                        {"statement_id": "event", "statement": "A recorded event."}
                    ]
                },
            },
            statement_evidence=evidence,
        )
    monkeypatch.undo()
    session.rollback()

    report = reconcile_publications(session, store=store, repair=True)
    session.commit()

    assert report.completed_pending + report.abandoned_pending >= 1
    if report.completed_pending:
        record = coverage_for_date(session, profile_date)
        assert record is not None, "repaired publication is absent from coverage"
        assert record.has_recorded_event is True


@pytest.mark.integration
def test_republishing_identical_content_still_heals_a_missing_entry(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    """Idempotent publication returns early; if that path skips coverage, an
    archive whose index was never built cannot be healed by re-running the
    publishers."""
    from app.un_wpp import publish_context_profile

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    profile_date = date(1983, 7, 4)
    publish_context_profile(session, store=store, profile_date=profile_date)
    session.commit()
    session.execute(delete_coverage_entries())
    session.commit()
    assert coverage_for_date(session, profile_date) is None

    publish_context_profile(session, store=store, profile_date=profile_date)
    session.commit()

    assert coverage_for_date(session, profile_date) is not None


@pytest.mark.integration
def test_rebuild_refuses_to_advertise_an_unreadable_artifact(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    """The day endpoint 503s on a missing artifact; coverage must not keep
    telling navigation that the date is worth visiting."""
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    profile_date = date(1984, 2, 2)
    run = start_batch_run(
        session,
        kind=CONTEXT_BATCH_KIND,
        requested={"dates": [profile_date.isoformat()]},
    )
    run_context_batch(session, store=store, dates=[profile_date], batch_run=run)
    session.commit()
    rebuild_coverage_index(session, store=store)
    session.commit()
    assert coverage_for_date(session, profile_date) is not None

    for artifact in (tmp_path / "published").rglob("*.json"):
        artifact.unlink()

    result = rebuild_coverage_index(session, store=store)
    session.commit()

    assert coverage_for_date(session, profile_date) is None
    assert result.unreadable == [profile_date]
    assert result.indexed == 0


@pytest.mark.integration
def test_publication_after_a_rebuild_keeps_the_index_generation(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    """A date written by an ordinary publication must not report an older
    generation than the summary claims the index is on."""
    from app.un_wpp import publish_context_profile

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    first = date(1985, 9, 9)
    second = date(1985, 9, 10)
    publish_context_profile(session, store=store, profile_date=first)
    session.commit()
    rebuild_coverage_index(session, store=store)
    rebuild_coverage_index(session, store=store)
    session.commit()
    generation = coverage_summary(session).index_version
    assert generation >= 2

    publish_context_profile(session, store=store, profile_date=second)
    session.commit()

    record = coverage_for_date(session, second)
    assert record is not None
    assert record.index_version == generation
    assert coverage_summary(session).index_version == generation


@pytest.mark.integration
def test_review_status_reflects_recorded_review_not_evidence_presence(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    """A recorded event is not a reviewed one. Per-date human review exists
    as editorial-selection data or it does not exist at all."""
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    context_date = date(1986, 1, 6)
    unreviewed_date = date(1986, 1, 8)
    run = start_batch_run(
        session,
        kind=CONTEXT_BATCH_KIND,
        requested={"dates": [context_date.isoformat()]},
    )
    run_context_batch(session, store=store, dates=[context_date], batch_run=run)
    publish_enriched(session, store, unreviewed_date, label="unreviewed-event")
    session.commit()
    rebuild_coverage_index(session, store=store)
    session.commit()

    context = coverage_for_date(session, context_date)
    assert context is not None
    assert context.review_status == "rule_selected"

    unreviewed = coverage_for_date(session, unreviewed_date)
    assert unreviewed is not None
    assert unreviewed.has_recorded_event is True
    assert unreviewed.review_status == "unreviewed"


@pytest.mark.integration
def test_a_human_editorial_decision_reads_as_reviewed(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    from app.governance import EditorialSelection, EditorialSelectionStatus
    from app.models import ResolvedClaim

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    profile_date = date(1987, 5, 5)
    publish_enriched(session, store, profile_date, label="human-reviewed")
    resolved = session.scalar(
        select(ResolvedClaim).where(
            ResolvedClaim.canonical_key == "test:human-reviewed"
        )
    )
    assert resolved is not None
    session.add(
        EditorialSelection(
            profile_date=profile_date,
            section_key="recorded_on_this_date",
            resolved_claim_id=resolved.id,
            status=EditorialSelectionStatus.SELECTED,
            decision_version=1,
            display_rank=1,
            rationale="A human considered this date.",
            reviewed_by="editor@example.invalid",
        )
    )
    session.commit()
    rebuild_coverage_index(session, store=store)
    session.commit()

    record = coverage_for_date(session, profile_date)
    assert record is not None
    assert record.review_status == "reviewed"


@pytest.mark.integration
def test_rebuild_does_not_overwrite_a_newer_publication(
    session: Session, tmp_path: Path, reviewed_un_wpp: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A correction published while a rebuild is walking its snapshot must
    win: the rebuild re-reads each date under the publication lock."""
    from app import coverage as coverage_module
    from app.un_wpp import publish_context_profile

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    profile_date = date(1988, 8, 8)
    publish_context_profile(session, store=store, profile_date=profile_date)
    session.commit()
    stale = list(coverage_module.latest_published_manifests(session))
    stale_versions = {manifest.version for manifest in stale}
    assert stale_versions == {1}

    publish_context_profile(
        session, store=store, profile_date=profile_date, force_new_version=True
    )
    session.commit()
    current = session.scalar(
        select(PublicationManifest.version)
        .where(PublicationManifest.profile_date == profile_date)
        .order_by(PublicationManifest.version.desc())
        .limit(1)
    )
    assert current == 2

    monkeypatch.setattr(
        coverage_module, "latest_published_manifests", lambda _session: stale
    )
    rebuild_coverage_index(session, store=store)
    session.commit()

    entry = session.scalar(
        select(CoverageEntry).where(CoverageEntry.profile_date == profile_date)
    )
    assert entry is not None
    indexed_version = session.scalar(
        select(PublicationManifest.version).where(
            PublicationManifest.id == entry.publication_manifest_id
        )
    )
    assert indexed_version == 2, "rebuild indexed a superseded manifest"


@pytest.mark.integration
def test_the_summary_does_not_scale_with_the_archive(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    """A constant-size public response must not load 27,759 JSONB blobs."""
    from sqlalchemy import event

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    dates = [date(1989, 4, day) for day in range(1, 9)]
    run = start_batch_run(
        session,
        kind=CONTEXT_BATCH_KIND,
        requested={"dates": [value.isoformat() for value in dates]},
    )
    run_context_batch(session, store=store, dates=dates, batch_run=run)
    session.commit()
    rebuild_coverage_index(session, store=store)
    session.commit()

    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        statements.append(statement)

    event.listen(session.get_bind(), "before_cursor_execute", record)
    try:
        summary = coverage_summary(session)
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", record)

    assert summary.total_published == len(dates)
    assert summary.earliest == dates[0]
    assert summary.latest == dates[-1]
    assert len(statements) <= 3, statements
    assert not any("coverage_entries.sections" in text for text in statements), (
        "the summary loaded per-date section blobs"
    )


@pytest.mark.integration
def test_a_long_quality_grade_does_not_fail_publication(
    session: Session, tmp_path: Path
) -> None:
    """Coverage is written after the artifact is promoted; a contract-valid
    grade must never turn a completed publication into a failure."""
    from app.models import ProfileType

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    profile_date = date(1979, 3, 3)
    grade = "provisional-B"
    claim = create_claim(
        session,
        source_release_id=_synthetic_release(session, "long-grade").id,
        source_record_locator="record:long-grade",
        claim_type="synthetic_assertion",
        assertion_text="A recorded event.",
    )
    resolved = resolve_claim(
        session,
        canonical_key="test:long-grade",
        resolved_value={"statement": "A recorded event."},
        rationale="Test-only recorded event.",
        supporting_claim_ids=[claim.id],
    )

    publish_day_profile(
        session,
        store=store,
        profile_date=profile_date,
        profile_type=ProfileType.STANDARD_STATISTICAL,
        payload={
            "schema_version": "1",
            "date": profile_date.isoformat(),
            "profile_type": "standard_statistical",
            "sections": {
                "recorded_on_this_date": [
                    {"statement_id": "event", "statement": "A recorded event."}
                ]
            },
            "quality": {"grade": grade, "explanation": "Longer than eight."},
        },
        statement_evidence=[
            PublicationStatementEvidenceInput(
                statement_path="/sections/recorded_on_this_date/0",
                resolved_claim_id=resolved.id,
            )
        ],
    )
    session.commit()

    record = coverage_for_date(session, profile_date)
    assert record is not None
    assert record.quality_floor == grade


def test_the_python_review_vocabulary_matches_the_shared_contract() -> None:
    """The contract binds the API and the UI; a status the UI cannot name is
    a status the API must not send."""
    import re

    from app.coverage import REVIEW_STATUSES

    contract = (
        Path(__file__).resolve().parents[3]
        / "packages"
        / "contracts"
        / "src"
        / "index.ts"
    ).read_text(encoding="utf-8")
    block = re.search(
        r"export const COVERAGE_REVIEW_STATUSES = \[(.*?)\] as const;",
        contract,
        re.DOTALL,
    )
    assert block is not None, "contract does not declare coverage review statuses"
    declared = tuple(re.findall(r'"([a-z_]+)"', block.group(1)))
    assert declared == REVIEW_STATUSES


@pytest.mark.integration
def test_a_blank_reviewer_is_not_a_human_review(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    """`reviewed_by` is NOT NULL but nothing forbids an empty string, and
    "not the standing rule" is not the same as "a person decided"."""
    from app.governance import EditorialSelection, EditorialSelectionStatus
    from app.models import ResolvedClaim

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    profile_date = date(1978, 11, 11)
    publish_enriched(session, store, profile_date, label="blank-reviewer")
    resolved = session.scalar(
        select(ResolvedClaim).where(
            ResolvedClaim.canonical_key == "test:blank-reviewer"
        )
    )
    assert resolved is not None
    session.add(
        EditorialSelection(
            profile_date=profile_date,
            section_key="recorded_on_this_date",
            resolved_claim_id=resolved.id,
            status=EditorialSelectionStatus.SELECTED,
            decision_version=1,
            display_rank=1,
            rationale="Selection with no recorded reviewer.",
            reviewed_by="   ",
        )
    )
    session.commit()
    rebuild_coverage_index(session, store=store)
    session.commit()

    record = coverage_for_date(session, profile_date)
    assert record is not None
    assert record.review_status == "unreviewed"


# --- Round 2 review findings (PR #43) ------------------------------------


def test_the_quality_floor_orders_grades_by_rank_not_alphabet() -> None:
    """`max()` over strings puts "A+" above "A", so the floor would report
    the stronger grade. The contract permits any grade string."""
    from app.coverage import quality_floor_from_payload

    def payload(*grades: str) -> dict[str, object]:
        return {
            "quality": {"grade": grades[0], "explanation": "aggregate"},
            "sections": {
                "recorded_on_this_date": [
                    {
                        "statement_id": f"s{index}",
                        "statement": "x",
                        "details": {"quality_grade": grade},
                    }
                    for index, grade in enumerate(grades[1:])
                ]
            },
        }

    assert quality_floor_from_payload(payload("A", "C", "B")) == "C"
    assert quality_floor_from_payload(payload("A+", "A")) == "A+"
    # An ungradeable value cannot be ordered, so it is treated as the
    # weakest thing present rather than silently ranked.
    assert quality_floor_from_payload(payload("A", "provisional")) == "provisional"
    assert quality_floor_from_payload(payload("B")) == "B"


@pytest.mark.integration
def test_a_rebuild_keeps_a_date_published_while_it_was_running(
    session: Session, tmp_path: Path, reviewed_un_wpp: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A date published after the snapshot is not in it, so the cleanup pass
    would delete the row publication had just written."""
    from app import coverage as coverage_module
    from app.un_wpp import publish_context_profile

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    existing = date(1991, 5, 5)
    published_mid_rebuild = date(1991, 5, 6)
    publish_context_profile(session, store=store, profile_date=existing)
    session.commit()
    snapshot = list(coverage_module.latest_published_manifests(session))

    publish_context_profile(session, store=store, profile_date=published_mid_rebuild)
    session.commit()
    assert coverage_for_date(session, published_mid_rebuild) is not None

    monkeypatch.setattr(
        coverage_module, "latest_published_manifests", lambda _session: snapshot
    )
    report = rebuild_coverage_index(session, store=store)
    session.commit()

    assert coverage_for_date(session, published_mid_rebuild) is not None, (
        "the rebuild deleted a date published while it ran"
    )
    assert report.dropped == 0


@pytest.mark.integration
def test_a_rebuild_does_not_hold_one_lock_per_date(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    """At archive scale a transaction-scoped lock per date exhausts the lock
    pool and blocks corrections for the length of the run."""
    from sqlalchemy import text

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    dates = [date(1992, 7, day) for day in range(1, 7)]
    run = start_batch_run(
        session,
        kind=CONTEXT_BATCH_KIND,
        requested={"dates": [value.isoformat() for value in dates]},
    )
    run_context_batch(session, store=store, dates=dates, batch_run=run)
    session.commit()

    rebuild_coverage_index(session, store=store)

    held = session.execute(
        text(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
            "AND pid = pg_backend_pid()"
        )
    ).scalar_one()
    session.commit()

    assert held == 0, f"{held} advisory locks still held after the rebuild"


@pytest.mark.integration
def test_reconcile_repair_indexes_the_recovered_quality_floor(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repair reads and verifies the artifact; discarding it leaves coverage
    describing the predecessor's grade under the new manifest."""
    from app.models import ProfileType
    from app.services import reconcile_publications

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    profile_date = date(1982, 2, 2)
    release = _synthetic_release(session, "recovered-floor")

    def evidence(label: str) -> list[PublicationStatementEvidenceInput]:
        claim = create_claim(
            session,
            source_release_id=release.id,
            source_record_locator=f"record:{label}",
            claim_type="synthetic_assertion",
            assertion_text=f"Event {label}.",
        )
        resolved = resolve_claim(
            session,
            canonical_key=f"test:recovered-{label}",
            resolved_value={"statement": f"Event {label}."},
            rationale="Test-only recorded event.",
            supporting_claim_ids=[claim.id],
        )
        return [
            PublicationStatementEvidenceInput(
                statement_path="/sections/recorded_on_this_date/0",
                resolved_claim_id=resolved.id,
            )
        ]

    def payload(grade: str) -> dict[str, object]:
        return {
            "schema_version": "1",
            "date": profile_date.isoformat(),
            "profile_type": "standard_statistical",
            "sections": {
                "recorded_on_this_date": [
                    {"statement_id": "event", "statement": f"Event {grade}."}
                ]
            },
            "quality": {"grade": grade, "explanation": "Grade under test."},
        }

    first = publish_day_profile(
        session,
        store=store,
        profile_date=profile_date,
        profile_type=ProfileType.STANDARD_STATISTICAL,
        payload=payload("B"),
        statement_evidence=evidence("first"),
    )
    session.commit()
    before = coverage_for_date(session, profile_date)
    assert before is not None
    assert before.quality_floor == "B"

    from app import services as services_module

    def explode(*args: object, **inner: object) -> None:
        raise RuntimeError("Simulated crash before artifact promotion.")

    monkeypatch.setattr(services_module.StagedProfileWrite, "finalize", explode)
    with pytest.raises(RuntimeError, match="Simulated crash"):
        publish_day_profile(
            session,
            store=store,
            profile_date=profile_date,
            profile_type=ProfileType.STANDARD_STATISTICAL,
            payload=payload("D"),
            statement_evidence=evidence("second"),
            supersedes_manifest_id=first.publication_manifest_id,
            supersedes_day_profile_id=first.id,
        )
    monkeypatch.undo()
    session.rollback()

    report = reconcile_publications(session, store=store, repair=True)
    session.commit()

    if report.completed_pending:
        record = coverage_for_date(session, profile_date)
        assert record is not None
        assert record.quality_floor == "D", (
            "coverage kept the predecessor's grade after repair"
        )
