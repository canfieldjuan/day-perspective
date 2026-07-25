from __future__ import annotations

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
    DerivedValueInput,
    Event,
    EventImpact,
    EventLocation,
    EventTime,
    ImpactDirectness,
    Metric,
    PipelineRun,
    QualityAssessment,
    QualityCheck,
    RawSourceRecord,
    ResolvedClaim,
    ResolvedClaimEvidence,
    SourceRelease,
    TemporalAssignment,
)
from app.services import supersede_claim
from app.ucdp import (
    LocalFilesystemRawSourceStore,
    build_ucdp_annual_profile_content,
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


def test_rejected_ucdp_annual_claim_blocks_review_before_resolution(
    session: Session, tmp_path: Path
) -> None:
    result = ingest_ucdp_annual(
        session,
        fixture_path=ANNUAL_FIXTURE,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )
    rejected = session.scalar(
        select(Claim)
        .where(
            Claim.source_release_id == result.source_release_id,
        )
        .order_by(Claim.source_record_locator)
    )
    assert rejected is not None
    rejected.assertion_status = ClaimAssertionStatus.REJECTED
    session.flush()

    with pytest.raises(
        ValueError, match="Non-accepted UCDP annual claims block review"
    ):
        review_ucdp_annual(session, result.source_release_id)

    assert rejected.assertion_status == ClaimAssertionStatus.REJECTED
    assert session.scalar(select(func.count()).select_from(ResolvedClaim)) == 0


def test_revised_ucdp_release_versions_resolutions_and_selects_current_context(
    session: Session, tmp_path: Path
) -> None:
    store = LocalFilesystemRawSourceStore(tmp_path / "raw")
    first = ingest_ucdp_annual(
        session, fixture_path=ANNUAL_FIXTURE, raw_store=store
    )
    first_derived = review_ucdp_annual(session, first.source_release_id)
    session.commit()

    revised_fixture = tmp_path / "revised-annual.csv"
    revised_fixture.write_text(
        ANNUAL_FIXTURE.read_text(encoding="utf-8").replace(
            '"India, Pakistan"', '"India and Pakistan"'
        ),
        encoding="utf-8",
    )
    second = ingest_ucdp_annual(
        session, fixture_path=revised_fixture, raw_store=store
    )
    second_derived = review_ucdp_annual(session, second.source_release_id)

    assert second_derived.id != first_derived.id
    latest = session.scalar(
        select(ResolvedClaim)
        .where(ResolvedClaim.canonical_key == "ucdp-prio:conflict:218:1964")
        .order_by(ResolvedClaim.version.desc())
    )
    assert latest is not None
    assert latest.version == 2
    assert latest.resolved_value["location"] == "India and Pakistan"
    supporting_release = session.scalar(
        select(Claim.source_release_id)
        .join(
            ResolvedClaimEvidence,
            ResolvedClaimEvidence.claim_id == Claim.id,
        )
        .where(
            ResolvedClaimEvidence.resolved_claim_id == latest.id,
            ResolvedClaimEvidence.stance == "supporting",
        )
    )
    assert supporting_release == second.source_release_id

    content = build_ucdp_annual_profile_content(session)
    assert content.source_release_id == second.source_release_id
    selected_derived = session.scalar(
        select(DerivedValue)
        .join(
            DerivedValueInput,
            DerivedValueInput.derived_value_id == DerivedValue.id,
        )
        .where(
            DerivedValueInput.resolved_claim_id.in_(
                [row.id for row in content.resolved_claims]
            ),
            DerivedValue.value_kind == "active_state_based_conflict_count",
        )
        .order_by(DerivedValue.created_at.desc())
    )
    assert selected_derived is not None
    assert selected_derived.id == second_derived.id
    metric = session.scalar(
        select(Metric).where(
            Metric.metric_key == "ucdp:active_state_based_conflict_count"
        )
    )
    assert metric is not None
    assert metric.provenance_resolved_claim_id in {
        row.id for row in content.resolved_claims
    }


def test_superseded_ucdp_input_blocks_stale_annual_publication(
    session: Session, tmp_path: Path
) -> None:
    result = ingest_ucdp_annual(
        session,
        fixture_path=ANNUAL_FIXTURE,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )
    review_ucdp_annual(session, result.source_release_id)
    prior = session.scalar(
        select(Claim)
        .where(Claim.source_release_id == result.source_release_id)
        .order_by(Claim.source_record_locator)
    )
    assert prior is not None
    supersede_claim(
        session,
        prior_claim=prior,
        assertion_text="Unreviewed corrected conflict-year record.",
        assertion_json=prior.assertion_json,
    )

    with pytest.raises(
        ValueError, match="exactly 25 current accepted claims"
    ):
        build_ucdp_annual_profile_content(session)


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


def test_ucdp_ged_quality_describes_equal_fatality_bounds_without_exaggeration(
    session: Session, tmp_path: Path
) -> None:
    equal_bounds = tmp_path / "equal-bounds-ged.csv"
    equal_bounds.write_text(
        GED_FIXTURE.read_text(encoding="utf-8").replace(
            ",100,100,1100,100,0,0,0",
            ",100,100,100,100,0,0,0",
        ),
        encoding="utf-8",
    )
    result = ingest_ucdp_ged(
        session,
        fixture_path=equal_bounds,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )
    review_ucdp_ged(session, result.source_release_id)
    assessment = session.scalar(
        select(QualityAssessment).where(
            QualityAssessment.assessment_kind == "ucdp_ged_event_quality_v1"
        )
    )

    assert assessment is not None
    assert assessment.public_explanation is not None
    assert "low 100, best 100, and high 100" in assessment.public_explanation
    assert "much larger" not in assessment.public_explanation


def test_rejected_ucdp_ged_claim_blocks_review_before_resolution(
    session: Session, tmp_path: Path
) -> None:
    result = ingest_ucdp_ged(
        session,
        fixture_path=GED_FIXTURE,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )
    rejected = session.scalar(
        select(Claim).where(
            Claim.source_release_id == result.source_release_id,
            Claim.claim_type == "fatalities",
        )
    )
    assert rejected is not None
    rejected.assertion_status = ClaimAssertionStatus.REJECTED
    session.flush()

    with pytest.raises(ValueError, match="Non-accepted UCDP GED claims"):
        review_ucdp_ged(session, result.source_release_id)

    assert session.scalar(select(func.count()).select_from(ResolvedClaim)) == 0


def test_revised_ucdp_ged_release_versions_and_refreshes_canonical_event(
    session: Session, tmp_path: Path
) -> None:
    store = LocalFilesystemRawSourceStore(tmp_path / "raw")
    first = ingest_ucdp_ged(session, fixture_path=GED_FIXTURE, raw_store=store)
    first_event = review_ucdp_ged(session, first.source_release_id)
    session.commit()

    revised = tmp_path / "revised-release-ged.csv"
    text = GED_FIXTURE.read_text(encoding="utf-8")
    text = text.replace("1989-01-26 00:00:00.000", "1989-01-27 00:00:00.000")
    text = text.replace(",100,100,1100,100,0,0,0", ",250,200,400,100,0,0,0")
    revised.write_text(text, encoding="utf-8")
    second = ingest_ucdp_ged(session, fixture_path=revised, raw_store=store)
    second_event = review_ucdp_ged(session, second.source_release_id)

    latest = session.scalar(
        select(ResolvedClaim)
        .where(ResolvedClaim.canonical_key == "ucdp-ged:6833:fatalities")
        .order_by(ResolvedClaim.version.desc())
    )
    assert latest is not None
    assert latest.version == 2
    assert session.scalar(select(func.count()).select_from(Event)) == 1
    assert second_event.id == first_event.id
    event_time = session.scalar(
        select(EventTime).where(EventTime.event_id == first_event.id)
    )
    impact = session.scalar(
        select(EventImpact).where(EventImpact.event_id == first_event.id)
    )
    assert event_time is not None
    assert event_time.start_date.isoformat() == "1989-01-27"
    assert event_time.provenance_resolved_claim_id != first_event.resolved_claim_id
    assert impact is not None
    assert impact.value_numeric == Decimal("250")
    assert impact.provenance_resolved_claim_id == latest.id


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


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [
        ("not-a-number", "33.066667"),
        ("91", "33.066667"),
        ("8.600000", "181"),
    ],
)
def test_ucdp_ged_rejects_invalid_coordinates_before_release(
    session: Session,
    tmp_path: Path,
    latitude: str,
    longitude: str,
) -> None:
    invalid = tmp_path / "invalid-ged-coordinates.csv"
    invalid.write_text(
        GED_FIXTURE.read_text(encoding="utf-8").replace(
            ",8.600000,33.066667,100,",
            f",{latitude},{longitude},100,",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="GED coordinates"):
        ingest_ucdp_ged(
            session,
            fixture_path=invalid,
            raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
        )

    assert session.scalar(select(func.count()).select_from(SourceRelease)) == 0
    assert session.scalar(select(PipelineRun.status)) == "failed"
    assert session.scalar(select(QualityCheck.status)) == "failed"


def test_ucdp_ged_quality_preserves_unknown_source_date_precision(
    session: Session, tmp_path: Path
) -> None:
    imprecise = tmp_path / "imprecise-ged.csv"
    imprecise.write_text(
        GED_FIXTURE.read_text(encoding="utf-8").replace(
            ",1,1,Nasir town",
            ",2,1,Nasir town",
        ),
        encoding="utf-8",
    )
    result = ingest_ucdp_ged(
        session,
        fixture_path=imprecise,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )
    review_ucdp_ged(session, result.source_release_id)
    assessment = session.scalar(select(QualityAssessment))

    assert assessment is not None
    assert assessment.findings["temporal_precision"] == "unknown"
    assert assessment.public_grade == "C"
    assert (assessment.public_explanation or "").startswith("Grade C:")
