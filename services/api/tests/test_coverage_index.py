"""Coverage index (epic #32, slice AA3).

Once every 1950-2025 date carries annual context, "is anything published?"
stops being a useful question. The index answers the questions that replace
it: how rich is this date, does it hold a recorded event, which strata have
content, and where is the nearest date worth travelling to.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

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
from app.models import CoverageEntry, PublicationTier
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


def publish_enriched(
    session: Session,
    store: LocalFilesystemPublishedProfileStore,
    profile_date: date,
    *,
    label: str,
) -> None:
    from app.models import LegalReviewStatus, ProfileType, Source
    from app.services import create_source_release

    # A dedicated single-record source per enriched date: the shared helper
    # uses a fixed slug (so it cannot be called twice), and reusing the UN
    # WPP release would demand per-claim source-record hashes.
    source = Source(
        slug=f"test-source-{label}",
        name=f"Synthetic source for {label}",
        publisher="Test suite",
        canonical_url=f"https://example.invalid/{label}",
        legal_review_status=LegalReviewStatus.NOT_REQUIRED,
    )
    session.add(source)
    session.flush()
    release = create_source_release(
        session,
        source_id=source.id,
        release_label=f"{label}-v1",
        source_url=f"https://example.invalid/{label}/v1",
        raw_storage_uri=f"test://raw/{label}-v1",
        raw_bytes=f"raw bytes for {label}".encode(),
        raw_record_count=1,
    )
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
    assert body["supported_range"] == {"min": "1900-01-01", "max": "2025-12-31"}

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
