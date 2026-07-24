from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.governance import SourceReleaseLicense
from app.models import (
    Claim,
    DataStatus,
    DerivedValueInput,
    Event,
    EventImpact,
    EventLocation,
    EventTime,
    ImpactDirectness,
    PipelineRun,
    QualityCheck,
    RawSourceRecord,
    SourceRelease,
    TemporalAssignment,
)
from app.ucdp import (
    LocalFilesystemRawSourceStore,
    ingest_ucdp_annual,
    ingest_ucdp_ged,
    review_ucdp_annual,
    review_ucdp_ged,
)

ROOT = Path(__file__).resolve().parents[3]
ANNUAL_FIXTURE = (
    ROOT / "data/fixtures/ucdp/ucdp-prio-26.1-conflicts-1964.csv"
)
GED_FIXTURE = ROOT / "data/fixtures/ucdp/ged-26.1-event-6833.csv"


def test_ucdp_annual_fixture_is_idempotent_and_derives_period_context(
    session: Session, tmp_path: Path
) -> None:
    store = LocalFilesystemRawSourceStore(tmp_path / "raw")
    first = ingest_ucdp_annual(
        session, fixture_path=ANNUAL_FIXTURE, raw_store=store
    )
    second = ingest_ucdp_annual(
        session, fixture_path=ANNUAL_FIXTURE, raw_store=store
    )

    assert first.record_count == 25
    assert first.claim_count == 25
    assert second.idempotent is True
    assert second.source_release_id == first.source_release_id
    assert session.scalar(select(func.count()).select_from(SourceRelease)) == 1
    assert session.scalar(select(func.count()).select_from(RawSourceRecord)) == 25
    assert session.scalar(select(func.count()).select_from(Claim)) == 25
    license_row = session.get(SourceReleaseLicense, first.source_release_id)
    assert license_row is not None
    assert license_row.license_identifier == "CC-BY-4.0"

    derived = review_ucdp_annual(session, first.source_release_id)
    assert derived.value_numeric == Decimal("25")
    assert derived.temporal_assignment == TemporalAssignment.PERIOD_CONTEXT
    assert derived.value_json is not None
    assert derived.value_json["date_specific"] is False
    assert (
        session.scalar(
            select(func.count())
            .select_from(DerivedValueInput)
            .where(DerivedValueInput.derived_value_id == derived.id)
        )
        == 25
    )


def test_ucdp_ged_fixture_builds_bounded_direct_event_impact(
    session: Session, tmp_path: Path
) -> None:
    result = ingest_ucdp_ged(
        session,
        fixture_path=GED_FIXTURE,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )
    event = review_ucdp_ged(session, result.source_release_id)

    assert session.scalar(select(func.count()).select_from(Event)) == 1
    event_time = session.scalar(
        select(EventTime).where(EventTime.event_id == event.id)
    )
    assert event_time is not None
    assert event_time.local_date is None
    assert event_time.exact_timestamp is None
    assert event_time.temporal_assignment == TemporalAssignment.DIRECT_RECORD
    assert (
        session.scalar(
            select(func.count())
            .select_from(EventLocation)
            .where(EventLocation.event_id == event.id)
        )
        == 1
    )
    impact = session.scalar(
        select(EventImpact).where(EventImpact.event_id == event.id)
    )
    assert impact is not None
    assert impact.value_numeric == Decimal("100")
    assert impact.impact_directness == ImpactDirectness.DIRECT
    assert impact.data_status == DataStatus.ESTIMATED
    fatality_claim = session.scalar(
        select(Claim).where(
            Claim.source_release_id == result.source_release_id,
            Claim.claim_type == "fatalities",
        )
    )
    assert fatality_claim is not None
    assert fatality_claim.lower_bound == Decimal("100")
    assert fatality_claim.upper_bound == Decimal("1100")


def test_ucdp_failure_records_failed_run_without_release(
    session: Session, tmp_path: Path
) -> None:
    invalid = tmp_path / "invalid.csv"
    invalid.write_text("wrong,columns\n1,2\n", encoding="utf-8")

    try:
        ingest_ucdp_annual(
            session,
            fixture_path=invalid,
            raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Invalid UCDP fixture should fail validation.")

    assert session.scalar(select(func.count()).select_from(SourceRelease)) == 0


def test_ucdp_ged_materializes_reviewed_fatality_values(
    session: Session, tmp_path: Path
) -> None:
    revised = tmp_path / "revised-ged.csv"
    revised.write_text(
        GED_FIXTURE.read_text(encoding="utf-8").replace(
            ",100,100,1100,100,0,0,0",
            ",250,200,400,100,0,0,0",
        ),
        encoding="utf-8",
    )
    result = ingest_ucdp_ged(
        session,
        fixture_path=revised,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )
    event = review_ucdp_ged(session, result.source_release_id)
    impact = session.scalar(
        select(EventImpact).where(EventImpact.event_id == event.id)
    )

    assert impact is not None
    assert impact.value_numeric == Decimal("250")
    assert impact.narrative == (
        "UCDP GED best estimate 250 direct deaths; low 200, high 400."
    )


def test_ucdp_ged_rejects_best_estimate_outside_bounds_before_release(
    session: Session, tmp_path: Path
) -> None:
    invalid = tmp_path / "invalid-ged-bounds.csv"
    invalid.write_text(
        GED_FIXTURE.read_text(encoding="utf-8").replace(
            ",100,100,1100,100,0,0,0",
            ",50,100,1100,100,0,0,0",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="0 <= low <= best <= high"):
        ingest_ucdp_ged(
            session,
            fixture_path=invalid,
            raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
        )

    assert session.scalar(select(func.count()).select_from(SourceRelease)) == 0
    assert session.scalar(select(PipelineRun.status)) == "failed"
    assert session.scalar(select(QualityCheck.status)) == "failed"
