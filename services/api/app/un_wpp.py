from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.base import LocalFilesystemRawSourceStore, RawSourceStore
from app.governance import (
    EditorialSelectionStatus,
    LicenseInput,
    ReviewDecisionValue,
    assert_release_publication_eligible,
    record_claim_review,
    record_editorial_selection,
    register_release_license,
)
from app.models import (
    Claim,
    ClaimAssertionStatus,
    ComparabilityStatus,
    DataStatus,
    DateRole,
    DerivedValue,
    DerivedValueInput,
    LegalReviewStatus,
    Methodology,
    Metric,
    MetricCoverage,
    Observation,
    PipelineRun,
    QualityAssessment,
    QualityCheck,
    RawSourceRecord,
    ResolutionMethod,
    ResolvedClaim,
    ResolvedClaimEvidence,
    ReviewTask,
    Source,
    SourceRelease,
    TemporalAssignment,
    TemporalPrecision,
)
from app.services import (
    PublicationStatementEvidenceInput,
    canonical_json_bytes,
    content_hash,
    create_claim,
    create_source_release,
    resolve_claim,
)

__all__ = ["LocalFilesystemRawSourceStore"]

UN_WPP_SOURCE_SLUG = "un-wpp-2024"
UN_WPP_SOURCE_URL = (
    "https://population.un.org/wpp/assets/Excel%20Files/"
    "1_Indicator%20(Standard)/EXCEL_FILES/1_General/"
    "WPP2024_GEN_F01_DEMOGRAPHIC_INDICATORS_COMPACT.xlsx"
)
UN_WPP_TERMS_URL = "https://creativecommons.org/licenses/by/3.0/igo/"
UN_WPP_LICENSE_SNAPSHOT = (
    "The official WPP 2024 GEN/01/REV1 workbook states that the United Nations "
    "work is available under CC BY 3.0 IGO with a suggested source citation."
)
GOLDEN_YEAR = 1964
GOLDEN_DATE = date(1964, 3, 27)
SELECTED_YEARS = frozenset({1950, 1964, 1989, 2023})

METRIC_DEFINITIONS = {
    "population_midyear": (
        "World mid-year population",
        "thousand persons",
        "Estimated population at 1 July of the calendar year.",
    ),
    "annual_births": (
        "World annual births",
        "thousand persons",
        "Estimated live births during the calendar year.",
    ),
    "annual_deaths": (
        "World annual deaths",
        "thousand persons",
        "Estimated deaths during the calendar year.",
    ),
    "life_expectancy": (
        "World life expectancy at birth",
        "years",
        "Estimated period life expectancy at birth for the calendar year.",
    ),
    "under_five_mortality": (
        "World under-five mortality",
        "deaths per 1,000 live births",
        "Estimated probability of dying before age five per 1,000 live births.",
    ),
}


@dataclass(frozen=True)
class WPPRecord:
    location: str
    location_code: str
    year: int
    variant: str
    population_july_thousands: Decimal
    births_thousands: Decimal
    deaths_thousands: Decimal
    life_expectancy_years: Decimal
    under_five_mortality_per_1000: Decimal

    @property
    def record_id(self) -> str:
        return f"{self.location_code}:{self.year}:{self.variant.lower()}"

    def canonical_value(self) -> dict[str, str | int]:
        return {
            "location": self.location,
            "location_code": self.location_code,
            "year": self.year,
            "variant": self.variant,
            "population_july_thousands": str(self.population_july_thousands),
            "births_thousands": str(self.births_thousands),
            "deaths_thousands": str(self.deaths_thousands),
            "life_expectancy_years": str(self.life_expectancy_years),
            "under_five_mortality_per_1000": str(
                self.under_five_mortality_per_1000
            ),
        }


@dataclass(frozen=True)
class WPPIngestionResult:
    pipeline_run_id: UUID
    source_release_id: UUID
    claim_count: int
    checksum: str
    idempotent: bool


@dataclass(frozen=True)
class WPPProfileContent:
    typical_statements: list[dict[str, object]]
    context_statements: list[dict[str, object]]
    evidence: list[PublicationStatementEvidenceInput]
    source_release_id: UUID
    methodology: Methodology
    resolved_claims: tuple[ResolvedClaim, ...]


def _parse(payload: bytes) -> tuple[WPPRecord, ...]:
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8")))
    required = {
        "location",
        "location_code",
        "year",
        "variant",
        "population_july_thousands",
        "births_thousands",
        "deaths_thousands",
        "life_expectancy_years",
        "under_five_mortality_per_1000",
    }
    if reader.fieldnames is None or set(reader.fieldnames) != required:
        raise ValueError("The WPP fixture schema does not match the selected release.")
    records: list[WPPRecord] = []
    for row in reader:
        record = WPPRecord(
            location=row["location"],
            location_code=row["location_code"],
            year=int(row["year"]),
            variant=row["variant"],
            population_july_thousands=Decimal(row["population_july_thousands"]),
            births_thousands=Decimal(row["births_thousands"]),
            deaths_thousands=Decimal(row["deaths_thousands"]),
            life_expectancy_years=Decimal(row["life_expectancy_years"]),
            under_five_mortality_per_1000=Decimal(
                row["under_five_mortality_per_1000"]
            ),
        )
        if (
            record.location != "World"
            or record.location_code != "900"
            or record.variant != "Estimates"
            or record.year < 1950
            or record.year > 2023
        ):
            raise ValueError("The WPP fixture contains an unsupported record.")
        if min(
            record.population_july_thousands,
            record.births_thousands,
            record.deaths_thousands,
            record.life_expectancy_years,
            record.under_five_mortality_per_1000,
        ) <= 0:
            raise ValueError("The WPP fixture contains a non-positive measure.")
        records.append(record)
    if not records or len({record.record_id for record in records}) != len(records):
        raise ValueError("The WPP fixture must contain unique selected records.")
    years = {record.year for record in records}
    if years != SELECTED_YEARS or len(records) != len(SELECTED_YEARS):
        raise ValueError(
            "The WPP fixture must contain exactly 1950, 1964, 1989, and 2023."
        )
    return tuple(records)


def _methodology(session: Session) -> Methodology:
    existing = session.scalar(
        select(Methodology).where(
            Methodology.slug == "un-wpp-annual-context",
            Methodology.version == "1",
        )
    )
    if existing is not None:
        return existing
    definition = {
        "source": "UN WPP 2024 estimates",
        "daily_equivalent": (
            "annual total in persons divided by the Gregorian day count for the year"
        ),
        "language": (
            "Average daily equivalent based on annual total; never a date observation."
        ),
    }
    row = Methodology(
        slug="un-wpp-annual-context",
        version="1",
        name="UN WPP annual context and daily-equivalent method",
        description=definition["language"],
        method_kind="annual_context_and_uniform_allocation",
        formula="annual_total_persons / gregorian_days_in_year",
        code_version="0.3.0",
        definition_hash=content_hash(definition),
        legal_review_status=LegalReviewStatus.NOT_REQUIRED,
    )
    session.add(row)
    session.flush()
    return row


def _register_license(session: Session, release_id: UUID) -> None:
    register_release_license(
        session,
        source_release_id=release_id,
        license_input=LicenseInput(
            license_identifier="CC-BY-3.0-IGO",
            license_snapshot=UN_WPP_LICENSE_SNAPSHOT,
            terms_url=UN_WPP_TERMS_URL,
            commercial_use_permission=True,
            redistribution_permission=True,
            derivatives_permission=True,
            attribution_required=True,
            attribution_text=(
                "United Nations DESA Population Division (2024), "
                "World Population Prospects 2024, CC BY 3.0 IGO."
            ),
            public_display_permission=True,
            raw_download_permission=True,
            terms_checked_at=date(2026, 7, 24),
            legal_review_status=LegalReviewStatus.NOT_REQUIRED,
        ),
    )


def ingest_un_wpp(
    session: Session,
    *,
    fixture_path: Path,
    raw_store: RawSourceStore,
) -> WPPIngestionResult:
    payload = fixture_path.read_bytes()
    checksum = hashlib.sha256(payload).hexdigest()
    run = PipelineRun(
        pipeline_name="un-wpp-2024-adapter",
        code_version="0.3.0",
        configuration_hash=content_hash(
            {
                "dataset": "GEN/01/REV1",
                "fixture": True,
                "source_url": UN_WPP_SOURCE_URL,
            }
        ),
        status="running",
        details={"mode": "fixture", "official_source_excerpt": True},
    )
    session.add(run)
    session.flush()
    try:
        with session.begin_nested():
            records = _parse(payload)
            source = session.scalar(
                select(Source).where(Source.slug == UN_WPP_SOURCE_SLUG)
            )
            if source is None:
                source = Source(
                    slug=UN_WPP_SOURCE_SLUG,
                    name="World Population Prospects 2024",
                    publisher=(
                        "United Nations, Department of Economic and Social Affairs, "
                        "Population Division"
                    ),
                    canonical_url="https://population.un.org/wpp/",
                    legal_review_status=LegalReviewStatus.NOT_REQUIRED,
                )
                session.add(source)
                session.flush()
            existing = session.scalar(
                select(SourceRelease).where(
                    SourceRelease.source_id == source.id,
                    SourceRelease.raw_checksum_sha256 == checksum,
                )
            )
            if existing is not None:
                _register_license(session, existing.id)
                run.status = "succeeded"
                run.completed_at = datetime.now(UTC)
                run.details = {**run.details, "idempotent": True}
                claim_count = len(
                    list(
                        session.scalars(
                            select(Claim.id).where(
                                Claim.source_release_id == existing.id
                            )
                        )
                    )
                )
                return WPPIngestionResult(
                    run.id, existing.id, claim_count, checksum, True
                )
            storage_uri = raw_store.write(UN_WPP_SOURCE_SLUG, checksum, payload)
            release = create_source_release(
                session,
                source_id=source.id,
                release_label=f"wpp2024-gen01-selected-{checksum[:12]}",
                source_url=UN_WPP_SOURCE_URL,
                raw_storage_uri=storage_uri,
                raw_bytes=payload,
                raw_record_count=len(records),
                pipeline_run_id=run.id,
                metadata_json={
                    "dataset": "World Population Prospects 2024",
                    "file_identity": "GEN/01/REV1",
                    "fixture": "official minimal excerpt",
                    "upstream_source_file_sha256": (
                        "98e34d9b65b53858cd08a57a566e45050b08093ad85ba5714fe6fbd78055ae6d"
                    ),
                    "variant": "Estimates",
                    "license": "CC-BY-3.0-IGO",
                },
                legal_review_status=LegalReviewStatus.NOT_REQUIRED,
            )
            _register_license(session, release.id)
            claim_count = 0
            metric_specs = (
                ("population_midyear", "population_july_thousands", "thousand persons"),
                ("annual_births", "births_thousands", "thousand persons"),
                ("annual_deaths", "deaths_thousands", "thousand persons"),
                ("life_expectancy", "life_expectancy_years", "years"),
                (
                    "under_five_mortality",
                    "under_five_mortality_per_1000",
                    "deaths per 1,000 live births",
                ),
            )
            for record in records:
                record_json = record.canonical_value()
                record_hash = hashlib.sha256(
                    canonical_json_bytes(record_json)
                ).hexdigest()
                locator = f"{UN_WPP_SOURCE_URL}#World-{record.year}"
                session.add(
                    RawSourceRecord(
                        source_release_id=release.id,
                        source_record_id=record.record_id,
                        source_record_locator=locator,
                        raw_storage_uri=storage_uri,
                        raw_checksum_sha256=record_hash,
                        schema_version="wpp2024-gen01-selected-v1",
                        payload_json=record_json,
                    )
                )
                for predicate, attribute, unit in metric_specs:
                    value = getattr(record, attribute)
                    claim = create_claim(
                        session,
                        source_release_id=release.id,
                        source_record_locator=locator,
                        source_record_hash_sha256=record_hash,
                        claim_type=predicate,
                        assertion_text=f"{value} {unit}",
                        assertion_json={
                            "value": str(value),
                            "unit": unit,
                            "year": record.year,
                            "geography": "World",
                            "variant": "Estimates",
                        },
                        assertion_status=ClaimAssertionStatus.CANDIDATE,
                        unit=unit,
                        lower_bound=value,
                        upper_bound=value,
                    )
                    claim.temporal_start = date(record.year, 1, 1)
                    claim.temporal_end = date(record.year, 12, 31)
                    claim.temporal_precision = TemporalPrecision.YEAR
                    claim.temporal_assignment = TemporalAssignment.DIRECT_RECORD
                    claim.date_role = DateRole.REPORTED
                    claim.data_status = DataStatus.ESTIMATED
                    claim.pipeline_run_id = run.id
                    session.add(
                        ReviewTask(
                            claim_id=claim.id,
                            status="open",
                            priority="normal",
                            rationale=(
                                f"Review UN WPP {predicate} estimate for {record.year}."
                            ),
                        )
                    )
                    claim_count += 1
            session.add(
                QualityCheck(
                    pipeline_run_id=run.id,
                    check_name="un_wpp_schema_selected_world_rows",
                    status="passed",
                    subject_type="source_release",
                    subject_id=release.id,
                    details={
                        "records": len(records),
                        "claims": claim_count,
                        "years": [record.year for record in records],
                    },
                )
            )
            run.status = "succeeded"
            run.completed_at = datetime.now(UTC)
            run.details = {**run.details, "idempotent": False, "checksum": checksum}
            return WPPIngestionResult(
                run.id, release.id, claim_count, checksum, False
            )
    except Exception as error:
        run.status = "failed"
        run.completed_at = datetime.now(UTC)
        run.details = {**run.details, "error": type(error).__name__}
        session.add(
            QualityCheck(
                pipeline_run_id=run.id,
                check_name="un_wpp_schema_selected_world_rows",
                status="failed",
                subject_type="pipeline_run",
                subject_id=run.id,
                details={"error": str(error)},
            )
        )
        session.flush()
        raise


def review_un_wpp(session: Session, source_release_id: UUID) -> int:
    claims = list(
        session.scalars(
            select(Claim)
            .where(Claim.source_release_id == source_release_id)
            .order_by(Claim.claim_type, Claim.temporal_start)
        )
    )
    if len(claims) != 20:
        raise ValueError("The selected WPP fixture must contain twenty claims.")
    blocked = [
        claim.claim_type
        for claim in claims
        if claim.assertion_status
        not in {
            ClaimAssertionStatus.CANDIDATE,
            ClaimAssertionStatus.IN_REVIEW,
            ClaimAssertionStatus.ACCEPTED,
        }
    ]
    if blocked:
        raise ValueError(
            "Non-accepted WPP claims block review: " + ", ".join(sorted(blocked))
        )
    methodology = _methodology(session)
    resolved_count = 0
    for claim in claims:
        if claim.assertion_status in {
            ClaimAssertionStatus.CANDIDATE,
            ClaimAssertionStatus.IN_REVIEW,
        }:
            record_claim_review(
                session,
                claim=claim,
                decision=ReviewDecisionValue.ACCEPTED,
                rationale="Matched the selected row and metric to WPP GEN/01/REV1.",
                reviewed_by="development-fixture-review",
            )
        if claim.assertion_status != ClaimAssertionStatus.ACCEPTED:
            raise ValueError("Every WPP claim must be accepted before resolution.")
        year = claim.temporal_start.year if claim.temporal_start is not None else 0
        canonical_key = f"un-wpp:world:{year}:{claim.claim_type}"
        prior = session.scalar(
            select(ResolvedClaim)
            .where(ResolvedClaim.canonical_key == canonical_key)
            .order_by(ResolvedClaim.version.desc())
        )
        prior_supports_current = (
            prior is not None
            and session.scalar(
                select(ResolvedClaimEvidence.claim_id).where(
                    ResolvedClaimEvidence.resolved_claim_id == prior.id,
                    ResolvedClaimEvidence.claim_id == claim.id,
                    ResolvedClaimEvidence.stance == "supporting",
                )
            )
            is not None
        )
        if (
            prior is None
            or prior.resolved_value != (claim.assertion_json or {})
            or not prior_supports_current
        ):
            resolved_row = resolve_claim(
                session,
                canonical_key=canonical_key,
                resolved_value=claim.assertion_json or {},
                rationale=(
                    "Accepted one attributed official UN WPP estimate. The estimate "
                    "status and annual resolution remain visible."
                ),
                supporting_claim_ids=[claim.id],
                resolution_method=ResolutionMethod.SINGLE_SOURCE,
                methodology_id=methodology.id,
                supersedes_resolved_claim_id=prior.id if prior is not None else None,
            )
            resolved_row.comparability_status = ComparabilityStatus.COMPARABLE
        else:
            resolved_row = prior
        display_name, unit, definition = METRIC_DEFINITIONS[claim.claim_type]
        metric = session.scalar(
            select(Metric).where(Metric.metric_key == f"un-wpp:{claim.claim_type}")
        )
        if metric is None:
            metric = Metric(
                metric_key=f"un-wpp:{claim.claim_type}",
                display_name=display_name,
                unit=unit,
                definition=definition,
                data_status=DataStatus.ESTIMATED,
                provenance_resolved_claim_id=resolved_row.id,
                methodology_id=methodology.id,
            )
            session.add(metric)
            session.flush()
        else:
            metric.provenance_resolved_claim_id = resolved_row.id
        observation = session.scalar(
            select(Observation).where(
                Observation.metric_id == metric.id,
                Observation.source_release_id == source_release_id,
                Observation.period_start == date(year, 1, 1),
            )
        )
        if observation is None:
            observation = Observation(
                metric_id=metric.id,
                source_release_id=source_release_id,
                provenance_resolved_claim_id=resolved_row.id,
                period_start=date(year, 1, 1),
                period_end=date(year, 12, 31),
                temporal_precision=TemporalPrecision.YEAR,
                temporal_assignment=TemporalAssignment.DIRECT_RECORD,
                date_role=DateRole.REPORTED,
                value_numeric=claim.lower_bound,
                data_status=DataStatus.ESTIMATED,
            )
            session.add(observation)
            session.flush()
        coverage = session.scalar(
            select(MetricCoverage).where(
                MetricCoverage.metric_id == metric.id,
                MetricCoverage.source_release_id == source_release_id,
                MetricCoverage.period_start == date(year, 1, 1),
            )
        )
        if coverage is None:
            session.add(
                MetricCoverage(
                    metric_id=metric.id,
                    source_release_id=source_release_id,
                    provenance_resolved_claim_id=resolved_row.id,
                    period_start=date(year, 1, 1),
                    period_end=date(year, 12, 31),
                    coverage_fraction=Decimal("1"),
                    data_status=DataStatus.ESTIMATED,
                    comparability_status=ComparabilityStatus.COMPARABLE,
                )
            )
        if year == GOLDEN_YEAR:
            section = (
                "typical_day_in_this_year"
                if claim.claim_type in {"annual_births", "annual_deaths"}
                else "wider_historical_context"
            )
            record_editorial_selection(
                session,
                profile_date=GOLDEN_DATE,
                section_key=section,
                resolved_claim_id=resolved_row.id,
                status=EditorialSelectionStatus.SELECTED,
                display_rank=resolved_count + 1,
                rationale="Selected official annual context for the golden date.",
                reviewed_by="development-fixture-review",
            )
        resolved_count += 1
    for task in session.scalars(
        select(ReviewTask).where(
            ReviewTask.claim_id.in_([claim.id for claim in claims]),
            ReviewTask.status.in_(("open", "in_progress")),
        )
    ):
        task.status = "resolved"
        task.completed_at = datetime.now(UTC)
    year_claims = {
        claim.claim_type: claim
        for claim in claims
        if claim.temporal_start is not None and claim.temporal_start.year == GOLDEN_YEAR
    }
    for predicate in ("annual_births", "annual_deaths"):
        claim = year_claims[predicate]
        resolved_input = session.scalar(
            select(ResolvedClaim)
            .where(
                ResolvedClaim.canonical_key
                == f"un-wpp:world:{GOLDEN_YEAR}:{predicate}"
            )
            .order_by(ResolvedClaim.version.desc())
        )
        assert resolved_input is not None
        annual_thousands = Decimal(str((claim.assertion_json or {})["value"]))
        days = Decimal("366" if GOLDEN_YEAR % 4 == 0 else "365")
        daily = (annual_thousands * Decimal("1000") / days).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
        fingerprint = content_hash(
            {
                "resolved_claim_id": str(resolved_input.id),
                "resolved_version": resolved_input.version,
                "days": str(days),
                "annual_thousands": str(annual_thousands),
            }
        )
        derived = session.scalar(
            select(DerivedValue).where(
                DerivedValue.value_kind == f"average_daily_{predicate[7:]}",
                DerivedValue.period_start == date(GOLDEN_YEAR, 1, 1),
                DerivedValue.input_fingerprint == fingerprint,
            )
        )
        if derived is None:
            daily_metric_key = f"app:average_daily_{predicate[7:]}"
            daily_metric = session.scalar(
                select(Metric).where(Metric.metric_key == daily_metric_key)
            )
            if daily_metric is None:
                daily_metric = Metric(
                    metric_key=daily_metric_key,
                    display_name=f"Average daily {predicate[7:]}",
                    unit="persons per day",
                    definition=(
                        "Uniform daily equivalent calculated from an annual total; "
                        "not a date-specific observation."
                    ),
                    data_status=DataStatus.MODELED,
                    provenance_resolved_claim_id=resolved_input.id,
                    methodology_id=methodology.id,
                )
                session.add(daily_metric)
                session.flush()
            else:
                daily_metric.provenance_resolved_claim_id = resolved_input.id
            derived = DerivedValue(
                metric_id=daily_metric.id,
                methodology_id=methodology.id,
                provenance_resolved_claim_id=resolved_input.id,
                value_kind=f"average_daily_{predicate[7:]}",
                period_start=date(GOLDEN_YEAR, 1, 1),
                period_end=date(GOLDEN_YEAR, 12, 31),
                temporal_assignment=TemporalAssignment.UNIFORM_PERIOD_ALLOCATION,
                value_numeric=daily,
                value_json={
                    "annual_total_thousands": str(annual_thousands),
                    "days_in_year": int(days),
                    "average_daily_equivalent": int(daily),
                    "display_precision": "nearest whole person",
                },
                data_status=DataStatus.ESTIMATED,
                comparability_status=ComparabilityStatus.COMPARABLE,
                input_fingerprint=fingerprint,
                calculation_version="0.3.0",
            )
            session.add(derived)
            session.flush()
            session.add(
                DerivedValueInput(
                    derived_value_id=derived.id,
                    resolved_claim_id=resolved_input.id,
                    input_role="primary",
                )
            )
        record_editorial_selection(
            session,
            profile_date=GOLDEN_DATE,
            section_key="typical_day_in_this_year",
            derived_value_id=derived.id,
            status=EditorialSelectionStatus.SELECTED,
            display_rank=1 if predicate == "annual_births" else 2,
            rationale="Selected annual total converted to a uniform daily equivalent.",
            reviewed_by="development-fixture-review",
        )
    existing_assessment = session.scalar(
        select(QualityAssessment).where(
            QualityAssessment.source_release_id == source_release_id,
            QualityAssessment.assessment_kind
            == "un_wpp_selected_context_quality_v1",
        )
    )
    if existing_assessment is None:
        session.add(
            QualityAssessment(
            source_release_id=source_release_id,
            methodology_id=methodology.id,
            assessment_kind="un_wpp_selected_context_quality_v1",
            findings={
                "source_resolution": "annual",
                "data_status": "estimated",
                "coverage": "World aggregate, selected fixture years",
                "daily_equivalent": "uniform allocation, not date-specific",
            },
            public_grade="B",
            public_explanation=(
                "Grade B: official annual UN estimates with clear methodology and "
                "coverage; daily values are transparent uniform equivalents, not "
                "observations for March 27."
            ),
            legal_review_status=LegalReviewStatus.NOT_REQUIRED,
            )
        )
    session.flush()
    return resolved_count


def _claim_provenance(
    *,
    claim: Claim,
    resolved: ResolvedClaim,
    release: SourceRelease,
    source: Source,
    methodology: Methodology,
) -> dict[str, object]:
    return {
        "root_type": "resolved_claim",
        "published_statement": "Selected annual UN demographic context.",
        "resolved_claim": {
            "canonical_key": resolved.canonical_key,
            "version": resolved.version,
            "method": resolved.resolution_method.value,
            "rationale": resolved.rationale,
        },
        "supporting_claims": [
            {
                "predicate": claim.claim_type,
                "value": claim.assertion_json,
                "source_record_locator": claim.source_record_locator,
                "source_record_hash_sha256": claim.source_record_hash_sha256,
            }
        ],
        "dissenting_claims": [],
        "source_release": {
            "source": source.name,
            "publisher": source.publisher,
            "release": release.release_label,
            "source_url": release.source_url,
            "raw_checksum_sha256": release.raw_checksum_sha256,
            "retrieved_at": release.retrieved_at.isoformat(),
        },
        "methodology": {
            "name": methodology.name,
            "version": methodology.version,
            "description": methodology.description,
        },
    }


def _derived_provenance(
    *,
    derived: DerivedValue,
    claim: Claim,
    release: SourceRelease,
    source: Source,
    methodology: Methodology,
) -> dict[str, object]:
    return {
        "root_type": "derived_value",
        "published_statement": "Uniform daily equivalent derived from an annual total.",
        "derived_value": {
            "kind": derived.value_kind,
            "calculation_version": derived.calculation_version,
            "value": derived.value_json,
        },
        "supporting_claims": [
            {
                "predicate": claim.claim_type,
                "value": claim.assertion_json,
                "source_record_locator": claim.source_record_locator,
                "source_record_hash_sha256": claim.source_record_hash_sha256,
            }
        ],
        "dissenting_claims": [],
        "source_release": {
            "source": source.name,
            "publisher": source.publisher,
            "release": release.release_label,
            "source_url": release.source_url,
            "raw_checksum_sha256": release.raw_checksum_sha256,
            "retrieved_at": release.retrieved_at.isoformat(),
        },
        "methodology": {
            "name": methodology.name,
            "version": methodology.version,
            "description": methodology.description,
        },
    }


def build_un_wpp_profile_content(session: Session) -> WPPProfileContent:
    source = session.scalar(
        select(Source).where(Source.slug == UN_WPP_SOURCE_SLUG)
    )
    if source is None:
        raise ValueError("UN WPP context has not been ingested.")
    release = session.scalar(
        select(SourceRelease)
        .where(SourceRelease.source_id == source.id)
        .order_by(SourceRelease.ingested_at.desc())
    )
    if release is None:
        raise ValueError("UN WPP context has no source release.")
    methodology = _methodology(session)
    claims = {
        claim.claim_type: claim
        for claim in session.scalars(
            select(Claim).where(
                Claim.source_release_id == release.id,
                Claim.temporal_start == date(GOLDEN_YEAR, 1, 1),
            )
        )
    }
    required_claims = {
        "population_midyear",
        "annual_births",
        "annual_deaths",
        "life_expectancy",
        "under_five_mortality",
    }
    if set(claims) != required_claims:
        raise ValueError("UN WPP selected-year claims are incomplete.")
    if any(
        claim.assertion_status != ClaimAssertionStatus.ACCEPTED
        for claim in claims.values()
    ):
        raise ValueError("The latest UN WPP release has not completed review.")
    if session.scalar(
        select(ReviewTask.id).where(
            ReviewTask.claim_id.in_([claim.id for claim in claims.values()]),
            ReviewTask.status.in_(("open", "in_progress")),
        )
    ) is not None:
        raise ValueError("The latest UN WPP release has pending review tasks.")
    resolved = {
        predicate: session.scalar(
            select(ResolvedClaim)
            .join(
                ResolvedClaimEvidence,
                ResolvedClaimEvidence.resolved_claim_id == ResolvedClaim.id,
            )
            .where(
                ResolvedClaimEvidence.claim_id == claim.id,
                ResolvedClaimEvidence.stance == "supporting",
            )
            .order_by(ResolvedClaim.version.desc())
        )
        for predicate, claim in claims.items()
    }
    if any(row is None for row in resolved.values()):
        raise ValueError("UN WPP selected claims have not all been resolved.")
    resolved_rows = {
        predicate: row for predicate, row in resolved.items() if row is not None
    }
    derived: dict[str, DerivedValue] = {}
    for row in session.scalars(
        select(DerivedValue)
        .where(
            DerivedValue.methodology_id == methodology.id,
            DerivedValue.period_start == date(GOLDEN_YEAR, 1, 1),
        )
        .order_by(DerivedValue.created_at.desc())
    ):
        predicate = {
            "average_daily_births": "annual_births",
            "average_daily_deaths": "annual_deaths",
        }.get(row.value_kind)
        if predicate is None or row.value_kind in derived:
            continue
        input_ids = set(
            session.scalars(
                select(DerivedValueInput.resolved_claim_id).where(
                    DerivedValueInput.derived_value_id == row.id
                )
            )
        )
        if input_ids == {resolved_rows[predicate].id}:
            derived[row.value_kind] = row
    required_derived = {"average_daily_births", "average_daily_deaths"}
    if not required_derived <= set(derived):
        raise ValueError("UN WPP daily equivalents have not been reviewed.")
    assert_release_publication_eligible(
        session,
        source_release_id=release.id,
        profile_date=GOLDEN_DATE,
        resolved_root_ids={row.id for row in resolved_rows.values()},
        derived_root_ids={derived[key].id for key in required_derived},
    )
    typical: list[dict[str, object]] = []
    evidence: list[PublicationStatementEvidenceInput] = []
    for index, (predicate, label) in enumerate(
        (("annual_births", "births"), ("annual_deaths", "deaths"))
    ):
        value = derived[f"average_daily_{label}"]
        daily = int(value.value_numeric or 0)
        typical.append(
            {
                "statement_id": f"average-daily-{label}",
                "statement": (
                    f"Average daily {label} in 1964: about {daily:,}. "
                    "This is an average daily equivalent based on the annual total, "
                    "not an observation for March 27."
                ),
                "details": {
                    **(value.value_json or {}),
                    "temporal_assignment": "uniform_period_allocation",
                    "data_status": "estimated",
                    "interpretation": (
                        "Average daily equivalent based on annual total. This is not "
                        "the number observed on March 27."
                    ),
                },
                "provenance_note": (
                    "UN WPP annual estimate divided by 366 days; not date-specific."
                ),
                "provenance": _derived_provenance(
                    derived=value,
                    claim=claims[predicate],
                    release=release,
                    source=source,
                    methodology=methodology,
                ),
            }
        )
        evidence.append(
            PublicationStatementEvidenceInput(
                statement_path=f"/sections/typical_day_in_this_year/{index}",
                derived_value_id=value.id,
            )
        )
    population_thousands = Decimal(
        str(resolved_rows["population_midyear"].resolved_value["value"])
    )
    life_expectancy = Decimal(
        str(resolved_rows["life_expectancy"].resolved_value["value"])
    )
    under_five_mortality = Decimal(
        str(resolved_rows["under_five_mortality"].resolved_value["value"])
    )
    context_specs = (
        (
            "population_midyear",
            "world-population",
            (
                "UN WPP estimates the mid-1964 world population at about "
                f"{population_thousands / Decimal('1000000'):.3f} billion."
            ),
        ),
        (
            "life_expectancy",
            "world-life-expectancy",
            (
                "UN WPP estimates global life expectancy at birth in 1964 at "
                f"{life_expectancy:.2f} years."
            ),
        ),
        (
            "under_five_mortality",
            "world-under-five-mortality",
            (
                "UN WPP estimates 1964 global under-five mortality at about "
                f"{under_five_mortality:.0f} deaths per 1,000 live births."
            ),
        ),
    )
    context: list[dict[str, object]] = []
    for index, (predicate, statement_id, statement) in enumerate(context_specs):
        claim = claims[predicate]
        resolved_row = resolved_rows[predicate]
        context.append(
            {
                "statement_id": statement_id,
                "statement": statement,
                "details": {
                    **(claim.assertion_json or {}),
                    "temporal_assignment": "direct_record",
                    "temporal_precision": "year",
                    "data_status": "estimated",
                },
                "provenance_note": "Official annual UN WPP estimate.",
                "provenance": _claim_provenance(
                    claim=claim,
                    resolved=resolved_row,
                    release=release,
                    source=source,
                    methodology=methodology,
                ),
            }
        )
        evidence.append(
            PublicationStatementEvidenceInput(
                statement_path=f"/sections/wider_historical_context/{index}",
                resolved_claim_id=resolved_row.id,
            )
        )
    return WPPProfileContent(
        typical_statements=typical,
        context_statements=context,
        evidence=evidence,
        source_release_id=release.id,
        methodology=methodology,
        resolved_claims=tuple(
            sorted(resolved_rows.values(), key=lambda row: row.canonical_key)
        ),
    )
