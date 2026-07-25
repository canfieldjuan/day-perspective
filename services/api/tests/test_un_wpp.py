from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.governance import SourceReleaseLicense
from app.models import (
    Claim,
    ClaimAssertionStatus,
    DataStatus,
    DerivedValue,
    PipelineRun,
    QualityCheck,
    RawSourceRecord,
    ResolvedClaim,
    ResolvedClaimEvidence,
    SourceRelease,
    TemporalAssignment,
)
from app.un_wpp import (
    LocalFilesystemRawSourceStore,
    build_un_wpp_profile_content,
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


def test_non_finite_wpp_measure_fails_before_release_creation(
    session: Session, tmp_path: Path
) -> None:
    with FIXTURE.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        rows = list(reader)
    assert fieldnames is not None
    next(row for row in rows if row["year"] == "1964")[
        "life_expectancy_years"
    ] = "Infinity"
    invalid_fixture = tmp_path / "non-finite-wpp.csv"
    with invalid_fixture.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    with pytest.raises(ValueError, match="non-finite"):
        ingest_un_wpp(
            session,
            fixture_path=invalid_fixture,
            raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
        )

    assert session.scalar(select(func.count()).select_from(SourceRelease)) == 0
    assert session.scalar(select(PipelineRun.status)) == "failed"


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


def test_rejected_wpp_claim_blocks_review_before_any_resolution(
    session: Session, tmp_path: Path
) -> None:
    result = ingest(session, tmp_path)
    rejected = session.scalar(
        select(Claim).where(
            Claim.source_release_id == result.source_release_id,
            Claim.claim_type == "annual_births",
        )
    )
    assert rejected is not None
    rejected.assertion_status = ClaimAssertionStatus.REJECTED
    session.flush()

    with pytest.raises(ValueError, match="Non-accepted WPP claims block review"):
        review_un_wpp(session, result.source_release_id)

    assert rejected.assertion_status == ClaimAssertionStatus.REJECTED
    assert session.scalar(select(func.count()).select_from(ResolvedClaim)) == 0


def test_revised_wpp_release_versions_resolution_and_daily_equivalent(
    session: Session, tmp_path: Path
) -> None:
    first = ingest(session, tmp_path)
    review_un_wpp(session, first.source_release_id)

    with FIXTURE.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        rows = list(reader)
    assert fieldnames is not None
    revised_births = next(row for row in rows if row["year"] == "1964")
    revised_births["births_thousands"] = "117658.2"
    revised_fixture = tmp_path / "revised-wpp.csv"
    with revised_fixture.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    second = ingest_un_wpp(
        session,
        fixture_path=revised_fixture,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )
    review_un_wpp(session, second.source_release_id)
    latest = session.scalar(
        select(ResolvedClaim)
        .where(ResolvedClaim.canonical_key == "un-wpp:world:1964:annual_births")
        .order_by(ResolvedClaim.version.desc())
    )
    assert latest is not None
    assert latest.version == 2
    assert latest.resolved_value["value"] == "117658.2"
    supporting_release = session.scalar(
        select(Claim.source_release_id)
        .join(ResolvedClaimEvidence, ResolvedClaimEvidence.claim_id == Claim.id)
        .where(
            ResolvedClaimEvidence.resolved_claim_id == latest.id,
            ResolvedClaimEvidence.stance == "supporting",
        )
    )
    assert supporting_release == second.source_release_id
    revised_daily = session.scalar(
        select(DerivedValue).where(
            DerivedValue.provenance_resolved_claim_id == latest.id,
            DerivedValue.value_kind == "average_daily_births",
        )
    )
    assert revised_daily is not None
    assert revised_daily.value_numeric == Decimal("321470")


def test_latest_unreviewed_wpp_release_blocks_profile_content(
    session: Session, tmp_path: Path
) -> None:
    first = ingest(session, tmp_path)
    review_un_wpp(session, first.source_release_id)
    session.commit()

    revised = tmp_path / "unreviewed-wpp.csv"
    revised.write_text(
        FIXTURE.read_text(encoding="utf-8").replace("117292.2", "117293.2"),
        encoding="utf-8",
    )
    ingest_un_wpp(
        session,
        fixture_path=revised,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )

    with pytest.raises(ValueError, match="has not completed review"):
        build_un_wpp_profile_content(session)


def test_wpp_context_prose_is_derived_from_current_reviewed_values(
    session: Session, tmp_path: Path
) -> None:
    revised = tmp_path / "revised-context-wpp.csv"
    with FIXTURE.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        rows = list(reader)
    assert fieldnames is not None
    selected = next(row for row in rows if row["year"] == "1964")
    selected["population_july_thousands"] = "4000000.0"
    selected["life_expectancy_years"] = "60.00"
    selected["under_five_mortality_per_1000"] = "150.0"
    with revised.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    result = ingest_un_wpp(
        session,
        fixture_path=revised,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )
    review_un_wpp(session, result.source_release_id)

    statements = {
        str(row["statement_id"]): str(row["statement"])
        for row in build_un_wpp_profile_content(session).context_statements
    }

    assert "4.000 billion" in statements["world-population"]
    assert "60.00 years" in statements["world-life-expectancy"]
    assert "150 deaths" in statements["world-under-five-mortality"]


def test_wpp_ingestion_rejects_a_fixture_missing_a_required_year(
    session: Session, tmp_path: Path
) -> None:
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    invalid = tmp_path / "missing-1964.csv"
    invalid.write_text(
        "\n".join(line for line in lines if ",1964," not in line) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="exactly 1950, 1964, 1989, and 2023"):
        ingest_un_wpp(
            session,
            fixture_path=invalid,
            raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
        )

    assert session.scalar(select(func.count()).select_from(SourceRelease)) == 0
    assert session.scalar(select(PipelineRun.status)) == "failed"
