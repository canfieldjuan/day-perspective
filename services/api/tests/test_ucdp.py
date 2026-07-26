from __future__ import annotations

from datetime import date
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

    # Still blocked, now by the accepted-status check rather than a fixed
    # count: supersession leaves an unreviewed claim for the year, and the
    # year's claims must all be accepted.
    with pytest.raises(ValueError, match="requires accepted claims for 1964"):
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


@pytest.mark.integration
def test_ucdp_annual_review_is_keyed_by_year_not_by_claim_count(
    session: Session, tmp_path: Path
) -> None:
    """Full UCDP coverage is blocked by fixture-shaped constraints, not by a
    real invariant: review required exactly 25 claims, and the canonical key
    and derivation hard-coded 1964."""
    from app.ucdp import ingest_ucdp_annual, review_ucdp_annual

    result = ingest_ucdp_annual(
        session,
        fixture_path=ANNUAL_FIXTURE,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )
    assert result.source_release_id is not None

    derived = review_ucdp_annual(session, result.source_release_id, year=1964)

    assert derived.period_start == date(1964, 1, 1)
    assert derived.value_numeric is not None and derived.value_numeric > 0
    # The count is of that year's records, not of every claim in the release.
    assert int(derived.value_numeric) == 25


@pytest.mark.integration
def test_a_year_without_claims_is_named_rather_than_miscounted(
    session: Session, tmp_path: Path
) -> None:
    """A year the dataset does not cover must fail by name. Counting zero
    would publish an absence as a fact."""
    from app.ucdp import ingest_ucdp_annual, review_ucdp_annual

    result = ingest_ucdp_annual(
        session,
        fixture_path=ANNUAL_FIXTURE,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )
    assert result.source_release_id is not None

    with pytest.raises(ValueError, match="1971"):
        review_ucdp_annual(session, result.source_release_id, year=1971)


@pytest.mark.integration
def test_annual_content_is_built_for_a_requested_year(
    session: Session, tmp_path: Path
) -> None:
    from app.ucdp import (
        build_ucdp_annual_profile_content,
        ingest_ucdp_annual,
        review_ucdp_annual,
    )

    result = ingest_ucdp_annual(
        session,
        fixture_path=ANNUAL_FIXTURE,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )
    assert result.source_release_id is not None
    review_ucdp_annual(session, result.source_release_id, year=1964)

    content = build_ucdp_annual_profile_content(session, year=1964)

    statement = content.statements[0]
    assert "1964" in str(statement["statement"])
    # Still period context, never an event on a date.
    assert "annual context" in str(statement["statement"])

    with pytest.raises(ValueError, match="1971"):
        build_ucdp_annual_profile_content(session, year=1971)


@pytest.mark.integration
def test_annual_context_records_no_provenance_for_an_unrelated_date(
    session: Session, tmp_path: Path
) -> None:
    """Reviewing a year must not file an editorial selection against the
    golden date. That would be a false audit record, not a cosmetic one."""
    from app.governance import EditorialSelection
    from app.ucdp import GOLDEN_DATE, ingest_ucdp_annual, review_ucdp_annual

    result = ingest_ucdp_annual(
        session,
        fixture_path=ANNUAL_FIXTURE,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )
    assert result.source_release_id is not None
    derived = review_ucdp_annual(session, result.source_release_id, year=1964)

    selections = list(
        session.scalars(
            select(EditorialSelection).where(
                EditorialSelection.derived_value_id == derived.id
            )
        )
    )
    assert selections, "the reviewed root must be recorded as selected"
    # Its own year, so the golden date is correct here — the point is that
    # the date follows the year rather than being fixed.
    assert all(
        selection.profile_date.year == 1964 for selection in selections
    )
    assert any(selection.profile_date == GOLDEN_DATE for selection in selections)


def test_annual_statement_text_names_no_single_date() -> None:
    """The statement serves every date in its year, so no date-specific
    copy may survive in it. Asserted against the source rather than one
    rendered example, so a new hard-coded date fails too."""
    from pathlib import Path as _Path

    source = _Path(__file__).resolve().parents[1] / "app" / "ucdp.py"
    text = source.read_text(encoding="utf-8")
    builder = text[text.index("def build_ucdp_annual_profile_content") :]
    for banned in ["March 27", "March 27, 1964", "Twenty-five"]:
        assert banned not in builder, f"date-specific copy survives: {banned}"


@pytest.mark.integration
def test_a_new_ucdp_payload_never_overwrites_the_prior_release(
    session: Session, tmp_path: Path
) -> None:
    """No silent replacement: a revised dataset becomes its own release with
    its own checksum, and the release the published archive already rests on
    is left exactly as it was."""
    from app.models import SourceRelease
    from app.ucdp import UCDP_ANNUAL_URL, ingest_ucdp_annual

    store = LocalFilesystemRawSourceStore(tmp_path / "raw")
    first = ingest_ucdp_annual(session, fixture_path=ANNUAL_FIXTURE, raw_store=store)
    assert first.source_release_id is not None
    original = session.get(SourceRelease, first.source_release_id)
    assert original is not None
    original_checksum = original.raw_checksum_sha256
    original_label = original.release_label

    revised = tmp_path / "revised.csv"
    revised.write_text(
        ANNUAL_FIXTURE.read_text(encoding="utf-8").replace(
            '"India, Pakistan"', '"India and Pakistan"'
        ),
        encoding="utf-8",
    )
    second = ingest_ucdp_annual(session, fixture_path=revised, raw_store=store)

    assert second.source_release_id != first.source_release_id
    unchanged = session.get(SourceRelease, first.source_release_id)
    assert unchanged is not None
    assert unchanged.raw_checksum_sha256 == original_checksum
    assert unchanged.release_label == original_label

    # Both releases exist under the pinned URL, each identified by its own
    # checksum, so which one a statement rests on is always recoverable.
    releases = list(
        session.scalars(
            select(SourceRelease).where(SourceRelease.source_url == UCDP_ANNUAL_URL)
        )
    )
    assert len({release.raw_checksum_sha256 for release in releases}) == 2


def _synthetic_multiyear_csv(
    rows: list[tuple[str, str]], version: str = "26.1"
) -> str:
    """A deliberately synthetic multi-year release.

    SYNTHETIC — not UCDP data. It exercises the generalized invariants,
    which the committed 1964 excerpt cannot because it covers one year. The
    excerpt stays the provenance canary; this never leaves the test suite
    and must never be published.
    """
    header = (
        "conflict_id,location,side_a,side_b,year,intensity_level,"
        "type_of_conflict,start_date,start_prec,region,version"
    )
    lines = [header]
    for conflict_id, year in rows:
        lines.append(
            f"{conflict_id},SyntheticLand,Government of SyntheticLand,"
            f"Synthetic Opposition,{year},1,3,1948-12-31,3,3,{version}"
        )
    return "\n".join(lines) + "\n"


@pytest.mark.integration
def test_multi_year_release_ingests_every_year(
    session: Session, tmp_path: Path
) -> None:
    from app.ucdp import ingest_ucdp_annual, review_ucdp_annual

    fixture = tmp_path / "synthetic-multiyear.csv"
    fixture.write_text(
        _synthetic_multiyear_csv(
            [("900", "1971"), ("901", "1971"), ("900", "1972"), ("902", "1983")]
        ),
        encoding="utf-8",
    )
    result = ingest_ucdp_annual(
        session,
        fixture_path=fixture,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )
    assert result.source_release_id is not None

    # The same conflict active in two years is two records, not a duplicate.
    first = review_ucdp_annual(session, result.source_release_id, year=1971)
    second = review_ucdp_annual(session, result.source_release_id, year=1972)
    third = review_ucdp_annual(session, result.source_release_id, year=1983)
    assert int(first.value_numeric or 0) == 2
    assert int(second.value_numeric or 0) == 1
    assert int(third.value_numeric or 0) == 1

    # A year the release does not cover is named, never counted as zero.
    with pytest.raises(ValueError, match="1990"):
        review_ucdp_annual(session, result.source_release_id, year=1990)


@pytest.mark.integration
def test_multi_year_ingestion_fails_closed_on_each_invariant(
    session: Session, tmp_path: Path
) -> None:
    """Each failure is fail-closed: a half-accepted release would put
    unverified records behind published statements."""
    from app.ucdp import ingest_ucdp_annual

    store = LocalFilesystemRawSourceStore(tmp_path / "raw")
    cases: list[tuple[str, str, str]] = [
        (
            "version-drift",
            _synthetic_multiyear_csv([("900", "1971")], version="27.0"),
            "must be version 26.1",
        ),
        (
            "duplicate-pair",
            _synthetic_multiyear_csv([("900", "1971"), ("900", "1971")]),
            "unique per conflict-year",
        ),
        (
            "year-out-of-range",
            _synthetic_multiyear_csv([("900", "1850")]),
            "1946-2025",
        ),
        (
            "non-numeric-year",
            _synthetic_multiyear_csv([("900", "not-a-year")]),
            "non-numeric year",
        ),
    ]
    for label, body, expected in cases:
        fixture = tmp_path / f"{label}.csv"
        fixture.write_text(body, encoding="utf-8")
        with pytest.raises(ValueError, match=expected):
            ingest_ucdp_annual(session, fixture_path=fixture, raw_store=store)
        session.rollback()


@pytest.mark.integration
def test_re_ingesting_the_same_release_is_idempotent(
    session: Session, tmp_path: Path
) -> None:
    from app.ucdp import ingest_ucdp_annual

    store = LocalFilesystemRawSourceStore(tmp_path / "raw")
    first = ingest_ucdp_annual(session, fixture_path=ANNUAL_FIXTURE, raw_store=store)
    second = ingest_ucdp_annual(session, fixture_path=ANNUAL_FIXTURE, raw_store=store)

    assert second.source_release_id == first.source_release_id


def test_annual_dataset_is_never_presented_as_battle_deaths() -> None:
    """UCDP/PRIO annual carries conflict presence, type and intensity across
    1946-2025. Battle-related deaths are a separate dataset covering
    1989-2025, and conflating them would extend a mortality claim nearly
    forty years past its evidence."""
    from pathlib import Path as _Path

    from app.ucdp import UCDP_ANNUAL_EXCLUDES

    assert "battle-related deaths" in UCDP_ANNUAL_EXCLUDES

    import re

    source = _Path(__file__).resolve().parents[1] / "app" / "ucdp.py"
    text = source.read_text(encoding="utf-8")
    start = text.index("def build_ucdp_annual_profile_content")
    # Only this function. Slicing to end-of-file swept in the GED code,
    # which is the battle-related-deaths dataset and legitimately counts
    # fatalities — the point is that the annual builder must not.
    following = re.search(r"\n(?:def |@)", text[start + 1 :])
    builder = text[start : start + 1 + following.start()] if following else text[start:]

    for banned in ["battle_deaths", "battle-related death", "fatalit", "death"]:
        assert banned not in builder.lower(), (
            f"annual context must not claim mortality: {banned}"
        )
