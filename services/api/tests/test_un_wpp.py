from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.governance import SourceReleaseLicense
from app.models import (
    Claim,
    DataStatus,
    DerivedValue,
    PipelineRun,
    QualityCheck,
    RawSourceRecord,
    SourceRelease,
    TemporalAssignment,
)
from app.un_wpp import (
    LocalFilesystemRawSourceStore,
    ingest_un_wpp,
    review_un_wpp,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "data/fixtures/un-wpp/wpp2024-world-selected-years.csv"


def ingest(session: Session, tmp_path: Path):
    return ingest_un_wpp(
        session,
        fixture_path=FIXTURE,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )


def test_fixture_ingestion_preserves_records_claims_license_run_and_check(
    session: Session, tmp_path: Path
) -> None:
    result = ingest(session, tmp_path)

    assert result.claim_count == 20
    assert session.scalar(select(func.count()).select_from(RawSourceRecord)) == 4
    assert session.scalar(select(func.count()).select_from(Claim)) == 20
    assert session.scalar(select(PipelineRun.status)) == "succeeded"
    assert session.scalar(select(QualityCheck.status)) == "passed"
    license_row = session.get(SourceReleaseLicense, result.source_release_id)
    assert license_row is not None
    assert license_row.license_identifier == "CC-BY-3.0-IGO"
    assert license_row.attribution_required is True


def test_idempotent_rerun_does_not_duplicate_release_records_or_claims(
    session: Session, tmp_path: Path
) -> None:
    first = ingest(session, tmp_path)
    second = ingest(session, tmp_path)

    assert second.idempotent is True
    assert first.source_release_id == second.source_release_id
    assert session.scalar(select(func.count()).select_from(SourceRelease)) == 1
    assert session.scalar(select(func.count()).select_from(RawSourceRecord)) == 4
    assert session.scalar(select(func.count()).select_from(Claim)) == 20


def test_review_derives_leap_year_daily_equivalents_without_date_claim(
    session: Session, tmp_path: Path
) -> None:
    result = ingest(session, tmp_path)
    assert review_un_wpp(session, result.source_release_id) == 20
    values = {
        row.value_kind: row
        for row in session.scalars(
            select(DerivedValue).where(
                DerivedValue.value_kind.in_(
                    ("average_daily_births", "average_daily_deaths")
                )
            )
        )
    }

    births = values["average_daily_births"]
    deaths = values["average_daily_deaths"]
    assert births.temporal_assignment == TemporalAssignment.UNIFORM_PERIOD_ALLOCATION
    assert births.data_status == DataStatus.ESTIMATED
    assert births.value_numeric == Decimal("320470")
    assert deaths.value_numeric == Decimal("127931")
    assert births.value_json is not None
    assert births.value_json["days_in_year"] == 366
    assert "date" not in births.value_json
