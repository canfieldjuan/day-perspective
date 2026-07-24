from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    DataStatus,
    DateRole,
    Event,
    EventLocation,
    EventTime,
    Geography,
    GeographyVersion,
    Metric,
    MetricCoverage,
    MissingReason,
    Observation,
    TemporalAssignment,
    TemporalPrecision,
)
from app.services import classify_period_allocation, create_claim, resolve_claim
from tests.helpers import source_release


def provenance(session: Session):
    release = source_release(session)
    claim = create_claim(
        session,
        source_release_id=release.id,
        source_record_locator="record:provenance",
        claim_type="synthetic_assertion",
        assertion_text="Synthetic provenance assertion.",
    )
    resolved = resolve_claim(
        session,
        canonical_key="test:provenance",
        resolved_value={"statement": "Synthetic resolution."},
        rationale="Test-only provenance.",
        supporting_claim_ids=[claim.id],
    )
    return release, resolved


@pytest.mark.integration
def test_event_can_have_multiple_dates_and_locations(session: Session) -> None:
    _, resolved = provenance(session)
    event = Event(
        resolved_claim_id=resolved.id,
        event_type="synthetic_event",
        canonical_title="Synthetic relationship test event",
    )
    session.add(event)
    session.flush()
    session.add_all(
        [
            EventTime(
                event_id=event.id,
                provenance_resolved_claim_id=resolved.id,
                start_date=date(1969, 1, 1),
                temporal_precision=TemporalPrecision.DAY,
                temporal_assignment=TemporalAssignment.DIRECT_RECORD,
                date_role=DateRole.OCCURRED,
                is_primary=True,
            ),
            EventTime(
                event_id=event.id,
                provenance_resolved_claim_id=resolved.id,
                start_date=date(1969, 1, 2),
                temporal_precision=TemporalPrecision.DAY,
                temporal_assignment=TemporalAssignment.REPORTED,
                date_role=DateRole.REPORTED,
            ),
        ]
    )
    first = Geography(stable_key="test-place-a", geography_kind="synthetic")
    second = Geography(stable_key="test-place-b", geography_kind="synthetic")
    session.add_all([first, second])
    session.flush()
    first_version = GeographyVersion(
        geography_id=first.id,
        provenance_resolved_claim_id=resolved.id,
        name="Synthetic place A",
        valid_from=date(1900, 1, 1),
    )
    second_version = GeographyVersion(
        geography_id=second.id,
        provenance_resolved_claim_id=resolved.id,
        name="Synthetic place B",
        valid_from=date(1900, 1, 1),
    )
    session.add_all([first_version, second_version])
    session.flush()
    session.add_all(
        [
            EventLocation(
                event_id=event.id,
                geography_version_id=first_version.id,
                provenance_resolved_claim_id=resolved.id,
            ),
            EventLocation(
                event_id=event.id,
                geography_version_id=second_version.id,
                provenance_resolved_claim_id=resolved.id,
                location_role="secondary",
            ),
        ]
    )
    session.commit()
    assert session.scalar(select(func.count()).select_from(EventTime).where(EventTime.event_id == event.id)) == 2
    assert session.scalar(select(func.count()).select_from(EventLocation).where(EventLocation.event_id == event.id)) == 2


@pytest.mark.integration
def test_missing_value_is_not_converted_to_zero(session: Session) -> None:
    release, resolved = provenance(session)
    metric = Metric(
        metric_key="synthetic-count",
        display_name="Synthetic count",
        unit="count",
        definition="Test-only metric.",
        provenance_resolved_claim_id=resolved.id,
    )
    session.add(metric)
    session.flush()
    zero = Observation(
        metric_id=metric.id,
        source_release_id=release.id,
        period_start=date(1969, 1, 1),
        temporal_precision=TemporalPrecision.DAY,
        temporal_assignment=TemporalAssignment.DIRECT_RECORD,
        value_numeric=Decimal("0"),
        data_status=DataStatus.FINAL,
    )
    missing = Observation(
        metric_id=metric.id,
        source_release_id=release.id,
        period_start=date(1969, 1, 2),
        temporal_precision=TemporalPrecision.DAY,
        temporal_assignment=TemporalAssignment.UNKNOWN,
        data_status=DataStatus.MISSING,
        missing_reason=MissingReason.NOT_AVAILABLE,
    )
    session.add_all([zero, missing])
    session.commit()
    assert zero.value_numeric == Decimal("0") and zero.missing_reason is None
    assert missing.value_numeric is None and missing.missing_reason == MissingReason.NOT_AVAILABLE


@pytest.mark.integration
def test_missing_metric_coverage_is_not_converted_to_zero(session: Session) -> None:
    release, resolved = provenance(session)
    metric = Metric(
        metric_key="synthetic-coverage",
        display_name="Synthetic coverage",
        unit="fraction",
        definition="Test-only coverage metric.",
        provenance_resolved_claim_id=resolved.id,
    )
    session.add(metric)
    session.flush()
    zero = MetricCoverage(
        metric_id=metric.id,
        source_release_id=release.id,
        provenance_resolved_claim_id=resolved.id,
        period_start=date(1969, 1, 1),
        period_end=date(1969, 1, 1),
        coverage_fraction=Decimal("0"),
        data_status=DataStatus.FINAL,
    )
    missing = MetricCoverage(
        metric_id=metric.id,
        source_release_id=release.id,
        provenance_resolved_claim_id=resolved.id,
        period_start=date(1969, 1, 2),
        period_end=date(1969, 1, 2),
        data_status=DataStatus.MISSING,
        missing_reason=MissingReason.NOT_AVAILABLE,
    )
    session.add_all([zero, missing])
    session.commit()
    assert zero.coverage_fraction == Decimal("0")
    assert missing.coverage_fraction is None


def test_uniform_period_allocation_has_its_own_classification() -> None:
    assert classify_period_allocation(allocated_uniformly=True) == TemporalAssignment.UNIFORM_PERIOD_ALLOCATION
    assert classify_period_allocation(allocated_uniformly=False) == TemporalAssignment.MODELED_PERIOD_ALLOCATION
