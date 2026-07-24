from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import UUID

from geoalchemy2.elements import WKTElement
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
    Event,
    EventImpact,
    EventLocation,
    EventTime,
    Geography,
    GeographyVersion,
    ImpactDirectness,
    LegalReviewStatus,
    Methodology,
    Metric,
    MetricCoverage,
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

UCDP_SOURCE_SLUG = "ucdp"
UCDP_DOWNLOAD_PAGE = "https://ucdp.uu.se/downloads/"
UCDP_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
UCDP_ANNUAL_URL = (
    "https://ucdp.uu.se/downloads/ucdpprio/ucdp-prio-acd-261-csv.zip"
)
UCDP_GED_URL = "https://ucdp.uu.se/downloads/ged/ged261-csv.zip"
GOLDEN_DATE = date(1964, 3, 27)
GED_FIXTURE_DATE = date(1989, 1, 26)


@dataclass(frozen=True)
class UCDPIngestionResult:
    pipeline_run_id: UUID
    source_release_id: UUID
    record_count: int
    claim_count: int
    checksum: str
    idempotent: bool


@dataclass(frozen=True)
class UCDPAnnualProfileContent:
    statements: list[dict[str, object]]
    evidence: list[PublicationStatementEvidenceInput]
    source_release_id: UUID
    methodology: Methodology
    resolved_claims: tuple[ResolvedClaim, ...]


def _source(session: Session) -> Source:
    row = session.scalar(select(Source).where(Source.slug == UCDP_SOURCE_SLUG))
    if row is not None:
        return row
    row = Source(
        slug=UCDP_SOURCE_SLUG,
        name="Uppsala Conflict Data Program",
        publisher="Uppsala University, Department of Peace and Conflict Research",
        canonical_url=UCDP_DOWNLOAD_PAGE,
        legal_review_status=LegalReviewStatus.NOT_REQUIRED,
    )
    session.add(row)
    session.flush()
    return row


def _methodology(session: Session) -> Methodology:
    row = session.scalar(
        select(Methodology).where(
            Methodology.slug == "ucdp-period-context",
            Methodology.version == "1",
        )
    )
    if row is not None:
        return row
    definition = {
        "annual_count": (
            "Count unique UCDP/PRIO conflict-year records in the selected year."
        ),
        "event_impacts": (
            "Preserve UCDP GED low, best, and high direct-death estimates; "
            "do not convert an absent event row to zero."
        ),
    }
    row = Methodology(
        slug="ucdp-period-context",
        version="1",
        name="UCDP annual context and event evidence",
        description=(
            "Separates annual conflict-year context from post-1989 event records."
        ),
        method_kind="period_aggregation_and_direct_event",
        formula="count(distinct conflict_id) by year",
        code_version="0.3.0",
        definition_hash=content_hash(definition),
        legal_review_status=LegalReviewStatus.NOT_REQUIRED,
    )
    session.add(row)
    session.flush()
    return row


def _license(session: Session, release_id: UUID) -> None:
    register_release_license(
        session,
        source_release_id=release_id,
        license_input=LicenseInput(
            license_identifier="CC-BY-4.0",
            license_snapshot=(
                "The official UCDP Dataset Download Center states that all current "
                "datasets are licensed CC BY 4.0 and may be used and redistributed "
                "with citation of the publications listed for each dataset."
            ),
            terms_url=UCDP_LICENSE_URL,
            commercial_use_permission=True,
            redistribution_permission=True,
            derivatives_permission=True,
            attribution_required=True,
            attribution_text=(
                "Uppsala Conflict Data Program (UCDP), version 26.1, CC BY 4.0. "
                "See the publication citations on the UCDP download page."
            ),
            public_display_permission=True,
            raw_download_permission=True,
            terms_checked_at=date(2026, 7, 24),
            legal_review_status=LegalReviewStatus.NOT_REQUIRED,
        ),
    )


def _rows(payload: bytes, required: tuple[str, ...]) -> tuple[dict[str, str], ...]:
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8")))
    if reader.fieldnames != list(required):
        raise ValueError("The UCDP fixture schema does not match the pinned excerpt.")
    rows = tuple(dict(row) for row in reader)
    if not rows:
        raise ValueError("The UCDP fixture is empty.")
    return rows


def _start_run(session: Session, name: str, dataset: str) -> PipelineRun:
    row = PipelineRun(
        pipeline_name=name,
        code_version="0.3.0",
        configuration_hash=content_hash(
            {"dataset": dataset, "fixture": True, "version": "26.1"}
        ),
        status="running",
        details={"mode": "fixture", "official_source_excerpt": True},
    )
    session.add(row)
    session.flush()
    return row


def _existing_result(
    session: Session,
    *,
    run: PipelineRun,
    source_id: UUID,
    checksum: str,
) -> UCDPIngestionResult | None:
    release = session.scalar(
        select(SourceRelease).where(
            SourceRelease.source_id == source_id,
            SourceRelease.raw_checksum_sha256 == checksum,
        )
    )
    if release is None:
        return None
    _license(session, release.id)
    run.status = "succeeded"
    run.completed_at = datetime.now(UTC)
    run.details = {**run.details, "idempotent": True}
    records = len(
        list(
            session.scalars(
                select(RawSourceRecord.id).where(
                    RawSourceRecord.source_release_id == release.id
                )
            )
        )
    )
    claims = len(
        list(
            session.scalars(
                select(Claim.id).where(Claim.source_release_id == release.id)
            )
        )
    )
    return UCDPIngestionResult(run.id, release.id, records, claims, checksum, True)


def ingest_ucdp_annual(
    session: Session, *, fixture_path: Path, raw_store: RawSourceStore
) -> UCDPIngestionResult:
    payload = fixture_path.read_bytes()
    checksum = hashlib.sha256(payload).hexdigest()
    run = _start_run(session, "ucdp-prio-annual-adapter", "UCDP/PRIO 26.1")
    try:
        with session.begin_nested():
            required = (
                "conflict_id",
                "location",
                "side_a",
                "side_b",
                "year",
                "intensity_level",
                "type_of_conflict",
                "start_date",
                "start_prec",
                "region",
                "version",
            )
            rows = _rows(payload, required)
            if (
                len(rows) != 25
                or any(row["year"] != "1964" or row["version"] != "26.1" for row in rows)
                or len({row["conflict_id"] for row in rows}) != len(rows)
            ):
                raise ValueError("The annual fixture must be the 25 unique 1964 rows.")
            source = _source(session)
            existing = _existing_result(
                session, run=run, source_id=source.id, checksum=checksum
            )
            if existing is not None:
                return existing
            storage_uri = raw_store.write(UCDP_SOURCE_SLUG, checksum, payload)
            release = create_source_release(
                session,
                source_id=source.id,
                release_label=f"ucdp-prio-26.1-1964-{checksum[:12]}",
                source_url=UCDP_ANNUAL_URL,
                raw_storage_uri=storage_uri,
                raw_bytes=payload,
                raw_record_count=len(rows),
                pipeline_run_id=run.id,
                metadata_json={
                    "dataset": "UCDP/PRIO Armed Conflict Dataset",
                    "quality_contract_version": "1",
                    "required_quality_checks": [
                        "ucdp_prio_1964_unique_conflict_years"
                    ],
                    "version": "26.1",
                    "fixture": "official minimal 1964 excerpt",
                    "upstream_archive_sha256": (
                        "5f951743222964674a446e32a5a871077b29bd13349588d85fc59953d89c878a"
                    ),
                    "license": "CC-BY-4.0",
                },
                legal_review_status=LegalReviewStatus.NOT_REQUIRED,
            )
            _license(session, release.id)
            for row in rows:
                record_id = f"conflict:{row['conflict_id']}:1964"
                record_hash = hashlib.sha256(canonical_json_bytes(row)).hexdigest()
                locator = f"{UCDP_ANNUAL_URL}#{record_id}"
                session.add(
                    RawSourceRecord(
                        source_release_id=release.id,
                        source_record_id=record_id,
                        source_record_locator=locator,
                        raw_storage_uri=storage_uri,
                        raw_checksum_sha256=record_hash,
                        schema_version="ucdp-prio-26.1-selected-v1",
                        payload_json=row,
                    )
                )
                claim = create_claim(
                    session,
                    source_release_id=release.id,
                    source_record_locator=locator,
                    source_record_hash_sha256=record_hash,
                    claim_type="active_state_based_conflict_year",
                    assertion_text=(
                        f"{row['side_a']} and {row['side_b']} were coded as an "
                        "active state-based conflict in 1964."
                    ),
                    assertion_json=row,
                    assertion_status=ClaimAssertionStatus.CANDIDATE,
                )
                claim.temporal_start = date(1964, 1, 1)
                claim.temporal_end = date(1964, 12, 31)
                claim.temporal_precision = TemporalPrecision.YEAR
                claim.temporal_assignment = TemporalAssignment.DIRECT_RECORD
                claim.date_role = DateRole.REPORTED
                claim.data_status = DataStatus.FINAL
                claim.pipeline_run_id = run.id
                session.add(
                    ReviewTask(
                        claim_id=claim.id,
                        status="open",
                        priority="normal",
                        rationale="Review one UCDP/PRIO 1964 conflict-year record.",
                    )
                )
            session.add(
                QualityCheck(
                    pipeline_run_id=run.id,
                    check_name="ucdp_prio_1964_unique_conflict_years",
                    status="passed",
                    subject_type="source_release",
                    subject_id=release.id,
                    details={"records": 25, "unique_conflict_ids": 25},
                )
            )
            run.status = "succeeded"
            run.completed_at = datetime.now(UTC)
            run.details = {**run.details, "idempotent": False, "checksum": checksum}
            return UCDPIngestionResult(run.id, release.id, 25, 25, checksum, False)
    except Exception as error:
        run.status = "failed"
        run.completed_at = datetime.now(UTC)
        run.details = {**run.details, "error": type(error).__name__}
        session.add(
            QualityCheck(
                pipeline_run_id=run.id,
                check_name="ucdp_prio_1964_unique_conflict_years",
                status="failed",
                subject_type="pipeline_run",
                subject_id=run.id,
                details={"error": str(error)},
            )
        )
        session.flush()
        raise


def review_ucdp_annual(session: Session, source_release_id: UUID) -> DerivedValue:
    claims = list(
        session.scalars(
            select(Claim)
            .where(Claim.source_release_id == source_release_id)
            .order_by(Claim.source_record_locator)
        )
    )
    if len(claims) != 25:
        raise ValueError("UCDP annual review requires 25 source claims.")
    reviewable_statuses = {
        ClaimAssertionStatus.CANDIDATE,
        ClaimAssertionStatus.IN_REVIEW,
        ClaimAssertionStatus.ACCEPTED,
    }
    if any(claim.assertion_status not in reviewable_statuses for claim in claims):
        raise ValueError(
            "Non-accepted UCDP annual claims block review before resolution."
        )
    methodology = _methodology(session)
    resolved: list[ResolvedClaim] = []
    for claim in claims:
        if claim.assertion_status in {
            ClaimAssertionStatus.CANDIDATE,
            ClaimAssertionStatus.IN_REVIEW,
        }:
            record_claim_review(
                session,
                claim=claim,
                decision=ReviewDecisionValue.ACCEPTED,
                rationale="Record matches the pinned UCDP/PRIO 26.1 excerpt.",
                reviewed_by="development-fixture-review",
            )
        conflict_id = str((claim.assertion_json or {})["conflict_id"])
        canonical_key = f"ucdp-prio:conflict:{conflict_id}:1964"
        prior = session.scalar(
            select(ResolvedClaim)
            .where(ResolvedClaim.canonical_key == canonical_key)
            .order_by(ResolvedClaim.version.desc())
        )
        current_claim_supports_prior = (
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
        if current_claim_supports_prior:
            assert prior is not None
            selected_resolution = prior
        else:
            selected_resolution = resolve_claim(
                session,
                canonical_key=canonical_key,
                resolved_value=claim.assertion_json or {},
                rationale=(
                    "Accepted one official UCDP conflict-year record as annual "
                    "context, not evidence of activity on every date in the year."
                ),
                supporting_claim_ids=[claim.id],
                resolution_method=ResolutionMethod.SINGLE_SOURCE,
                methodology_id=methodology.id,
                supersedes_resolved_claim_id=prior.id if prior is not None else None,
            )
            selected_resolution.comparability_status = (
                ComparabilityStatus.COMPARABLE
            )
        resolved.append(selected_resolution)
    for task in session.scalars(
        select(ReviewTask).where(
            ReviewTask.claim_id.in_([claim.id for claim in claims]),
            ReviewTask.status.in_(("open", "in_progress")),
        )
    ):
        task.status = "resolved"
        task.completed_at = datetime.now(UTC)
    fingerprint = content_hash(
        {
            "resolved_claims": sorted(
                f"{row.id}:{row.version}" for row in resolved
            ),
            "year": 1964,
            "operation": "count unique conflict-year records",
        }
    )
    derived = session.scalar(
        select(DerivedValue).where(
            DerivedValue.value_kind == "active_state_based_conflict_count",
            DerivedValue.period_start == date(1964, 1, 1),
            DerivedValue.input_fingerprint == fingerprint,
        )
    )
    if derived is None:
        metric = session.scalar(
            select(Metric).where(
                Metric.metric_key == "ucdp:active_state_based_conflict_count"
            )
        )
        if metric is None:
            metric = Metric(
                metric_key="ucdp:active_state_based_conflict_count",
                display_name="Active state-based armed conflicts",
                unit="conflict-year records",
                definition=(
                    "Unique UCDP/PRIO state-based conflict records active in a "
                    "calendar year; not a date-specific count."
                ),
                data_status=DataStatus.FINAL,
                provenance_resolved_claim_id=resolved[0].id,
                methodology_id=methodology.id,
            )
            session.add(metric)
            session.flush()
        derived = DerivedValue(
            metric_id=metric.id,
            methodology_id=methodology.id,
            provenance_resolved_claim_id=resolved[0].id,
            value_kind="active_state_based_conflict_count",
            period_start=date(1964, 1, 1),
            period_end=date(1964, 12, 31),
            temporal_assignment=TemporalAssignment.PERIOD_CONTEXT,
            value_numeric=Decimal(len(resolved)),
            value_json={
                "year": 1964,
                "count": len(resolved),
                "unit": "conflict-year records",
                "date_specific": False,
            },
            data_status=DataStatus.FINAL,
            comparability_status=ComparabilityStatus.COMPARABLE,
            input_fingerprint=fingerprint,
            calculation_version="0.3.0",
        )
        session.add(derived)
        session.flush()
        for row in resolved:
            session.add(
                DerivedValueInput(
                    derived_value_id=derived.id,
                    resolved_claim_id=row.id,
                    input_role="primary",
                )
            )
        session.add(
            MetricCoverage(
                metric_id=metric.id,
                source_release_id=source_release_id,
                provenance_resolved_claim_id=resolved[0].id,
                period_start=date(1964, 1, 1),
                period_end=date(1964, 12, 31),
                coverage_fraction=Decimal("1"),
                data_status=DataStatus.FINAL,
                comparability_status=ComparabilityStatus.COMPARABLE,
            )
        )
    record_editorial_selection(
        session,
        profile_date=GOLDEN_DATE,
        section_key="wider_historical_context",
        derived_value_id=derived.id,
        status=EditorialSelectionStatus.SELECTED,
        display_rank=10,
        rationale="Selected as annual conflict context, not date-specific evidence.",
        reviewed_by="development-fixture-review",
    )
    existing_quality = session.scalar(
        select(QualityAssessment).where(
            QualityAssessment.source_release_id == source_release_id,
            QualityAssessment.assessment_kind == "ucdp_prio_annual_context_v1",
        )
    )
    if existing_quality is None:
        session.add(
            QualityAssessment(
                source_release_id=source_release_id,
                methodology_id=methodology.id,
                assessment_kind="ucdp_prio_annual_context_v1",
                findings={
                    "temporal_resolution": "conflict-year",
                    "coverage": "state-based armed conflicts meeting UCDP criteria",
                    "not_date_specific": True,
                },
                public_grade="B",
                public_explanation=(
                    "Grade B: an official, versioned conflict-year dataset with "
                    "documented definitions. The count describes 1964 as a whole "
                    "and does not establish conditions on March 27."
                ),
                legal_review_status=LegalReviewStatus.NOT_REQUIRED,
            )
        )
    session.flush()
    return derived


def build_ucdp_annual_profile_content(session: Session) -> UCDPAnnualProfileContent:
    source = session.scalar(select(Source).where(Source.slug == UCDP_SOURCE_SLUG))
    if source is None:
        raise ValueError("UCDP annual source has not been ingested.")
    release = session.scalar(
        select(SourceRelease)
        .where(
            SourceRelease.source_id == source.id,
            SourceRelease.source_url == UCDP_ANNUAL_URL,
        )
        .order_by(SourceRelease.ingested_at.desc())
    )
    if release is None:
        raise ValueError("UCDP annual release has not been ingested.")
    derived = session.scalar(
        select(DerivedValue)
        .join(
            DerivedValueInput,
            DerivedValueInput.derived_value_id == DerivedValue.id,
        )
        .join(
            ResolvedClaim,
            ResolvedClaim.id == DerivedValueInput.resolved_claim_id,
        )
        .join(
            ResolvedClaimEvidence,
            ResolvedClaimEvidence.resolved_claim_id == ResolvedClaim.id,
        )
        .join(Claim, Claim.id == ResolvedClaimEvidence.claim_id)
        .where(
            DerivedValue.value_kind == "active_state_based_conflict_count",
            DerivedValue.period_start == date(1964, 1, 1),
            Claim.source_release_id == release.id,
            ResolvedClaimEvidence.stance == "supporting",
        )
        .distinct()
    )
    if derived is None:
        raise ValueError("UCDP annual context has not been reviewed and derived.")
    methodology = session.get(Methodology, derived.methodology_id)
    if methodology is None:
        raise ValueError("UCDP annual methodology is missing.")
    inputs = list(
        session.scalars(
            select(ResolvedClaim)
            .join(
                DerivedValueInput,
                DerivedValueInput.resolved_claim_id == ResolvedClaim.id,
            )
            .where(DerivedValueInput.derived_value_id == derived.id)
        )
    )
    supporting_claims = list(
        session.scalars(
            select(Claim)
            .join(
                ResolvedClaimEvidence,
                ResolvedClaimEvidence.claim_id == Claim.id,
            )
            .where(
                ResolvedClaimEvidence.resolved_claim_id.in_(
                    [row.id for row in inputs]
                ),
                ResolvedClaimEvidence.stance == "supporting",
                Claim.source_release_id == release.id,
            )
            .order_by(Claim.source_record_locator)
        )
    )
    assert_release_publication_eligible(
        session,
        source_release_id=release.id,
        profile_date=GOLDEN_DATE,
        resolved_root_ids=set(),
        derived_root_ids={derived.id},
    )
    statement: dict[str, object] = {
        "statement_id": "ucdp-1964-active-conflicts",
        "statement": (
            f"UCDP/PRIO records {int(derived.value_numeric or 0)} state-based "
            "armed conflicts as active at some point in 1964. This is annual "
            "context, not a March 27 count."
        ),
        "details": {
            "title": "State-based armed conflicts active in 1964",
            "value": int(derived.value_numeric or 0),
            "unit": "conflict-year records",
            "temporal_assignment": TemporalAssignment.PERIOD_CONTEXT.value,
            "data_status": derived.data_status.value,
            "missing_data_explanation": (
                "This source does not provide a day-specific worldwide conflict "
                "count for March 27, 1964."
            ),
        },
        "provenance_note": (
            "Official UCDP/PRIO conflict-year records aggregated by a versioned "
            "application methodology."
        ),
        "provenance": {
            "root_type": "derived_value",
            "published_statement": (
                "Twenty-five catalogued conflict-year records are counted for 1964."
            ),
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
                    "source_record_hash_sha256": (
                        claim.source_record_hash_sha256
                    ),
                }
                for claim in supporting_claims
            ],
            "dissenting_claims": [],
            "source_release": {
                "source": source.name,
                "publisher": source.publisher,
                "release": release.release_label,
                "source_url": release.source_url,
                "raw_checksum_sha256": release.raw_checksum_sha256,
                "retrieved_at": release.ingested_at.isoformat(),
            },
            "methodology": {
                "name": methodology.name,
                "version": methodology.version,
                "description": methodology.description,
            },
        },
    }
    return UCDPAnnualProfileContent(
        statements=[statement],
        evidence=[
            PublicationStatementEvidenceInput(
                statement_path="/sections/wider_historical_context/3",
                derived_value_id=derived.id,
            )
        ],
        source_release_id=release.id,
        methodology=methodology,
        resolved_claims=tuple(inputs),
    )


def ingest_ucdp_ged(
    session: Session, *, fixture_path: Path, raw_store: RawSourceStore
) -> UCDPIngestionResult:
    payload = fixture_path.read_bytes()
    checksum = hashlib.sha256(payload).hexdigest()
    run = _start_run(session, "ucdp-ged-adapter", "UCDP GED 26.1")
    try:
        with session.begin_nested():
            required = (
                "id",
                "relid",
                "year",
                "active_year",
                "type_of_violence",
                "conflict_new_id",
                "conflict_name",
                "dyad_new_id",
                "dyad_name",
                "side_a",
                "side_b",
                "country",
                "country_id",
                "region",
                "date_start",
                "date_end",
                "date_prec",
                "where_prec",
                "where_coordinates",
                "where_description",
                "latitude",
                "longitude",
                "best",
                "low",
                "high",
                "deaths_a",
                "deaths_b",
                "deaths_civilians",
                "deaths_unknown",
            )
            rows = _rows(payload, required)
            if len(rows) != 1 or rows[0]["id"] != "6833":
                raise ValueError("The GED fixture must contain event 6833 only.")
            row = rows[0]
            interval_start = datetime.fromisoformat(row["date_start"]).date()
            interval_end = datetime.fromisoformat(row["date_end"]).date()
            source_date_precision = int(row["date_prec"])
            temporal_precision = (
                TemporalPrecision.DAY
                if source_date_precision == 1
                else TemporalPrecision.UNKNOWN
            )
            try:
                latitude = Decimal(row["latitude"])
                longitude = Decimal(row["longitude"])
            except InvalidOperation as error:
                raise ValueError(
                    "GED coordinates must be finite numeric values."
                ) from error
            if (
                not latitude.is_finite()
                or not longitude.is_finite()
                or not Decimal("-90") <= latitude <= Decimal("90")
                or not Decimal("-180") <= longitude <= Decimal("180")
            ):
                raise ValueError(
                    "GED coordinates must be finite and within latitude/longitude bounds."
                )
            low = int(row["low"])
            best = int(row["best"])
            high = int(row["high"])
            if low < 0 or not low <= best <= high:
                raise ValueError(
                    "The GED fatality estimate must satisfy 0 <= low <= best <= high."
                )
            source = _source(session)
            existing = _existing_result(
                session, run=run, source_id=source.id, checksum=checksum
            )
            if existing is not None:
                return existing
            storage_uri = raw_store.write(UCDP_SOURCE_SLUG, checksum, payload)
            release = create_source_release(
                session,
                source_id=source.id,
                release_label=f"ucdp-ged-26.1-event-6833-{checksum[:12]}",
                source_url=UCDP_GED_URL,
                raw_storage_uri=storage_uri,
                raw_bytes=payload,
                raw_record_count=1,
                pipeline_run_id=run.id,
                metadata_json={
                    "dataset": "UCDP Georeferenced Event Dataset Global",
                    "quality_contract_version": "1",
                    "required_quality_checks": [
                        "ucdp_ged_event_6833_schema_and_bounds"
                    ],
                    "version": "26.1",
                    "fixture": "official minimal event excerpt",
                    "upstream_archive_sha256": (
                        "8c941d84954e555ee2e54f40fa04d9203bf1e2f962203d0a9930966c4947c667"
                    ),
                    "license": "CC-BY-4.0",
                },
                legal_review_status=LegalReviewStatus.NOT_REQUIRED,
            )
            _license(session, release.id)
            record_hash = hashlib.sha256(canonical_json_bytes(row)).hexdigest()
            locator = f"{UCDP_GED_URL}#event-6833"
            session.add(
                RawSourceRecord(
                    source_release_id=release.id,
                    source_record_id="ged:6833",
                    source_record_locator=locator,
                    raw_storage_uri=storage_uri,
                    raw_checksum_sha256=record_hash,
                    schema_version="ucdp-ged-26.1-selected-v1",
                    payload_json=row,
                )
            )
            claims = (
                ("event_identity", {"ucdp_ged_id": 6833, "relid": row["relid"]}),
                ("event_type", {"type_of_violence": int(row["type_of_violence"])}),
                (
                    "occurrence_interval",
                    {
                        "start": row["date_start"],
                        "end": row["date_end"],
                        "precision": source_date_precision,
                    },
                ),
                (
                    "epicenter_geography",
                    {
                        "country": row["country"],
                        "country_id": row["country_id"],
                        "place": row["where_coordinates"],
                        "where_precision": int(row["where_prec"]),
                    },
                ),
                (
                    "coordinates",
                    {
                        "latitude": str(latitude),
                        "longitude": str(longitude),
                    },
                ),
                (
                    "fatalities",
                    {
                        "best": int(row["best"]),
                        "low": int(row["low"]),
                        "high": int(row["high"]),
                        "directness": "direct",
                    },
                ),
                (
                    "name",
                    {
                        "title": (
                            f"{row['dyad_name']} organized-violence event near "
                            f"{row['where_coordinates']}"
                        )
                    },
                ),
            )
            for predicate, value in claims:
                claim = create_claim(
                    session,
                    source_release_id=release.id,
                    source_record_locator=locator,
                    source_record_hash_sha256=record_hash,
                    claim_type=predicate,
                    assertion_text=str(value),
                    assertion_json=value,
                    assertion_status=ClaimAssertionStatus.CANDIDATE,
                    unit="persons" if predicate == "fatalities" else None,
                    lower_bound=Decimal(row["low"]) if predicate == "fatalities" else None,
                    upper_bound=Decimal(row["high"]) if predicate == "fatalities" else None,
                )
                claim.temporal_start = interval_start
                claim.temporal_end = interval_end
                claim.temporal_precision = temporal_precision
                claim.temporal_assignment = TemporalAssignment.DIRECT_RECORD
                claim.date_role = DateRole.OCCURRED
                claim.data_status = (
                    DataStatus.ESTIMATED
                    if predicate == "fatalities"
                    else DataStatus.FINAL
                )
                claim.pipeline_run_id = run.id
                session.add(
                    ReviewTask(
                        claim_id=claim.id,
                        status="open",
                        priority="normal",
                        rationale=f"Review UCDP GED event 6833 {predicate} claim.",
                    )
                )
            session.add(
                QualityCheck(
                    pipeline_run_id=run.id,
                    check_name="ucdp_ged_event_6833_schema_and_bounds",
                    status="passed",
                    subject_type="source_release",
                    subject_id=release.id,
                    details={
                        "record_count": 1,
                        "claim_count": len(claims),
                        "fatality_bounds_ordered": True,
                    },
                )
            )
            run.status = "succeeded"
            run.completed_at = datetime.now(UTC)
            run.details = {**run.details, "idempotent": False, "checksum": checksum}
            return UCDPIngestionResult(
                run.id, release.id, 1, len(claims), checksum, False
            )
    except Exception as error:
        run.status = "failed"
        run.completed_at = datetime.now(UTC)
        run.details = {**run.details, "error": type(error).__name__}
        session.add(
            QualityCheck(
                pipeline_run_id=run.id,
                check_name="ucdp_ged_event_6833_schema_and_bounds",
                status="failed",
                subject_type="pipeline_run",
                subject_id=run.id,
                details={"error": str(error)},
            )
        )
        session.flush()
        raise


def review_ucdp_ged(session: Session, source_release_id: UUID) -> Event:
    claims = list(
        session.scalars(
            select(Claim).where(Claim.source_release_id == source_release_id)
        )
    )
    if {claim.claim_type for claim in claims} != {
        "event_identity",
        "event_type",
        "occurrence_interval",
        "epicenter_geography",
        "coordinates",
        "fatalities",
        "name",
    }:
        raise ValueError("UCDP GED review requires the complete seven-claim record.")
    reviewable_statuses = {
        ClaimAssertionStatus.CANDIDATE,
        ClaimAssertionStatus.IN_REVIEW,
        ClaimAssertionStatus.ACCEPTED,
    }
    if any(claim.assertion_status not in reviewable_statuses for claim in claims):
        raise ValueError(
            "Non-accepted UCDP GED claims block review before resolution."
        )
    methodology = _methodology(session)
    identity_claim = next(
        claim for claim in claims if claim.claim_type == "event_identity"
    )
    resolved: dict[str, ResolvedClaim] = {}
    for claim in claims:
        if claim.assertion_status in {
            ClaimAssertionStatus.CANDIDATE,
            ClaimAssertionStatus.IN_REVIEW,
        }:
            record_claim_review(
                session,
                claim=claim,
                decision=ReviewDecisionValue.ACCEPTED,
                rationale="Record matches UCDP GED 26.1 event 6833.",
                reviewed_by="development-fixture-review",
            )
        canonical_key = f"ucdp-ged:6833:{claim.claim_type}"
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
        if prior_supports_current:
            assert prior is not None
            row = prior
        else:
            row = resolve_claim(
                session,
                canonical_key=canonical_key,
                resolved_value=claim.assertion_json or {},
                rationale=(
                    "Accepted one attributed UCDP GED record; fatality uncertainty "
                    "bounds remain part of the resolved value."
                ),
                supporting_claim_ids=[claim.id],
                resolution_method=ResolutionMethod.SINGLE_SOURCE,
                methodology_id=methodology.id,
                supersedes_resolved_claim_id=prior.id if prior is not None else None,
            )
            row.comparability_status = ComparabilityStatus.COMPARABLE
        resolved[claim.claim_type] = row
    for task in session.scalars(
        select(ReviewTask).where(
            ReviewTask.claim_id.in_([claim.id for claim in claims]),
            ReviewTask.status.in_(("open", "in_progress")),
        )
    ):
        task.status = "resolved"
        task.completed_at = datetime.now(UTC)
    event = session.scalar(
        select(Event)
        .join(ResolvedClaim, Event.resolved_claim_id == ResolvedClaim.id)
        .where(ResolvedClaim.canonical_key == "ucdp-ged:6833:event_identity")
    )
    title = str(resolved["name"].resolved_value["title"])
    if event is None:
        event = Event(
            resolved_claim_id=resolved["event_identity"].id,
            event_type="organized_violence",
            canonical_title=title,
            summary=(
                "UCDP GED event-level record with bounded direct fatality estimate."
            ),
            data_status=DataStatus.FINAL,
        )
        session.add(event)
        session.flush()
    event.resolved_claim_id = resolved["event_identity"].id
    event.event_type = "organized_violence"
    event.canonical_title = title
    event.summary = "UCDP GED event-level record with bounded direct fatality estimate."
    event.data_status = DataStatus.FINAL

    occurrence = resolved["occurrence_interval"].resolved_value
    interval_start = datetime.fromisoformat(str(occurrence["start"])).date()
    interval_end = datetime.fromisoformat(str(occurrence["end"])).date()
    temporal_precision = (
        TemporalPrecision.DAY
        if int(occurrence["precision"]) == 1
        else TemporalPrecision.UNKNOWN
    )
    event_time = session.scalar(
        select(EventTime).where(EventTime.event_id == event.id, EventTime.is_primary)
    )
    if event_time is None:
        event_time = EventTime(
            event_id=event.id,
            provenance_resolved_claim_id=resolved["occurrence_interval"].id,
            start_date=interval_start,
            temporal_precision=temporal_precision,
            temporal_assignment=TemporalAssignment.DIRECT_RECORD,
            date_role=DateRole.OCCURRED,
            is_primary=True,
        )
        session.add(event_time)
    event_time.provenance_resolved_claim_id = resolved["occurrence_interval"].id
    event_time.start_date = interval_start
    event_time.end_date = interval_end
    event_time.temporal_precision = temporal_precision
    event_time.display_label = (
        f"UCDP source-record date: {interval_start.strftime('%B %-d, %Y')}"
        if interval_start == interval_end
        else (
            "UCDP source-record interval: "
            f"{interval_start.isoformat()} to {interval_end.isoformat()}"
        )
    )
    event_time.interpretation = (
        "A source-reported date or interval; no exact timestamp or timezone "
        "conversion is asserted."
    )

    geography_value = resolved["epicenter_geography"].resolved_value
    geography = session.scalar(
        select(Geography).where(
            Geography.stable_key == f"ucdp-country:{geography_value['country_id']}"
        )
    )
    if geography is None:
        geography = Geography(
            stable_key=f"ucdp-country:{geography_value['country_id']}",
            geography_kind="historical_country",
        )
        session.add(geography)
        session.flush()
    geography_version = session.scalar(
        select(GeographyVersion).where(
            GeographyVersion.geography_id == geography.id,
            GeographyVersion.valid_from == date(1985, 4, 6),
        )
    )
    if geography_version is None:
        geography_version = GeographyVersion(
            geography_id=geography.id,
            provenance_resolved_claim_id=resolved["epicenter_geography"].id,
            name=f"{geography_value['country']} (1985-2011 boundaries)",
            identifier_code=f"UCDP-{geography_value['country_id']}",
            valid_from=date(1985, 4, 6),
            valid_to=date(2011, 7, 8),
        )
        session.add(geography_version)
        session.flush()
    geography_version.provenance_resolved_claim_id = resolved["epicenter_geography"].id
    geography_version.name = (
        f"{geography_value['country']} (1985-2011 boundaries)"
    )

    coordinates = resolved["coordinates"].resolved_value
    event_location = session.scalar(
        select(EventLocation).where(EventLocation.event_id == event.id)
    )
    if event_location is None:
        event_location = EventLocation(
            event_id=event.id,
            provenance_resolved_claim_id=resolved["coordinates"].id,
            location_role="reported_event_location",
        )
        session.add(event_location)
    event_location.geography_version_id = geography_version.id
    event_location.provenance_resolved_claim_id = resolved["coordinates"].id
    event_location.point_geometry = WKTElement(
        f"POINT({coordinates['longitude']} {coordinates['latitude']})", srid=4326
    )
    event_location.display_label = (
        f"{geography_value['place']}, {geography_value['country']}"
    )

    fatality_metric = session.scalar(
        select(Metric).where(Metric.metric_key == "ucdp:direct_event_deaths")
    )
    if fatality_metric is None:
        fatality_metric = Metric(
            metric_key="ucdp:direct_event_deaths",
            display_name="Direct deaths in organized-violence event",
            unit="persons",
            definition=(
                "UCDP GED best estimate of direct deaths for an event, with "
                "low and high bounds preserved in the supporting claim."
            ),
            data_status=DataStatus.ESTIMATED,
            provenance_resolved_claim_id=resolved["fatalities"].id,
            methodology_id=methodology.id,
        )
        session.add(fatality_metric)
        session.flush()
    fatality_metric.provenance_resolved_claim_id = resolved["fatalities"].id
    fatalities = resolved["fatalities"].resolved_value
    fatality_best = int(fatalities["best"])
    fatality_low = int(fatalities["low"])
    fatality_high = int(fatalities["high"])
    impact = session.scalar(
        select(EventImpact).where(
            EventImpact.event_id == event.id,
            EventImpact.metric_id == fatality_metric.id,
        )
    )
    if impact is None:
        impact = EventImpact(
            event_id=event.id,
            metric_id=fatality_metric.id,
            provenance_resolved_claim_id=resolved["fatalities"].id,
            methodology_id=methodology.id,
            impact_directness=ImpactDirectness.DIRECT,
            data_status=DataStatus.ESTIMATED,
        )
        session.add(impact)
    impact.provenance_resolved_claim_id = resolved["fatalities"].id
    impact.narrative = (
        f"UCDP GED best estimate {fatality_best:,} direct deaths; "
        f"low {fatality_low:,}, high {fatality_high:,}."
    )
    impact.value_numeric = Decimal(fatality_best)
    existing_quality = session.scalar(
        select(QualityAssessment).where(
            QualityAssessment.claim_id == identity_claim.id,
            QualityAssessment.assessment_kind == "ucdp_ged_event_quality_v1",
        )
    )
    if existing_quality is None:
        session.add(
            QualityAssessment(
                source_release_id=source_release_id,
                claim_id=identity_claim.id,
                methodology_id=methodology.id,
                assessment_kind="ucdp_ged_event_quality_v1",
                findings={
                    "temporal_precision": temporal_precision.value,
                    "geographic_precision": "named town and coordinates",
                    "measurement_directness": "direct deaths",
                    "source_agreement": "single source",
                    "fatality_bounds": {
                        "low": fatality_low,
                        "best": fatality_best,
                        "high": fatality_high,
                    },
                },
                public_grade=(
                    "B" if temporal_precision == TemporalPrecision.DAY else "C"
                ),
                public_explanation=(
                    (
                        "Grade B: official event-level UCDP record with day and place "
                        "precision. It is single-source evidence and the fatality high "
                        "bound is much larger than the best estimate."
                    )
                    if temporal_precision == TemporalPrecision.DAY
                    else (
                        "Grade C: official event-level UCDP record with place detail, "
                        "but the source interval does not support day precision. It is "
                        "single-source evidence and the fatality high bound is much "
                        "larger than the best estimate."
                    )
                ),
                legal_review_status=LegalReviewStatus.NOT_REQUIRED,
            )
        )
    session.flush()
    return event
