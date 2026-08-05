from __future__ import annotations

import hashlib
import math
import urllib.request
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from geoalchemy2.elements import WKTElement
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.base import (
    ClaimDraft,
    IngestionResult,
    LocalFilesystemRawSourceStore,
    RawSourceStore,
    SourceMetadata,
)
from app.conflict_comparison import optional_conflict_comparison
from app.governance import (
    EditorialSelectionStatus,
    LicenseInput,
    ReviewDecisionValue,
    assert_release_publication_eligible,
    events_behind_manifest,
    lineage_root_ids,
    record_claim_review,
    record_editorial_selection,
    register_release_license,
    reviewed_resolutions_for_release,
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
    EventLocation,
    EventTime,
    Geography,
    GeographyVersion,
    LegalReviewStatus,
    Methodology,
    PipelineRun,
    ProfileType,
    PublicationManifest,
    PublicationStatus,
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
    LocalFilesystemPublishedProfileStore,
    PublicationStatementEvidenceInput,
    RecordedEventBinding,
    canonical_json_bytes,
    content_hash,
    create_claim,
    create_source_release,
    publish_day_profile,
    resolve_claim,
)
from app.ucdp import build_ucdp_annual_profile_content
from app.un_wpp import build_un_wpp_profile_content

__all__ = ["LocalFilesystemRawSourceStore"]

USGS_SOURCE_SLUG = "usgs-earthquake-catalog"
USGS_EVENT_ID = "official19640328033616_30"
USGS_RECORD_LOCATOR = (
    "https://earthquake.usgs.gov/earthquakes/eventpage/"
    "official19640328033616_30"
)
USGS_QUERY_URL = (
    "https://earthquake.usgs.gov/fdsnws/event/1/query"
    "?format=geojson&starttime=1964-03-27&endtime=1964-03-29&minmagnitude=8"
)
GOLDEN_DATE = date(1964, 3, 27)
ALASKA_TIMEZONE = "America/Anchorage"
USGS_TERMS_URL = (
    "https://www.usgs.gov/information-policies-and-instructions/"
    "copyrights-and-credits"
)
USGS_LICENSE_SNAPSHOT = (
    "USGS-authored catalog data are United States public-domain government data; "
    "USGS requests source credit and notes that unrelated third-party website "
    "assets can have different rights."
)


def _display_number(value: object) -> str:
    rendered = format(Decimal(str(value)), "f")
    integer, separator, fractional = rendered.partition(".")
    if not separator:
        return integer
    trimmed_fractional = fractional.rstrip("0")
    return (
        integer
        if not trimmed_fractional
        else f"{integer}.{trimmed_fractional}"
    )


def _register_usgs_license(session: Session, release_id: UUID) -> None:
    register_release_license(
        session,
        source_release_id=release_id,
        license_input=LicenseInput(
            license_identifier="US-PD-USGS",
            license_snapshot=USGS_LICENSE_SNAPSHOT,
            terms_url=USGS_TERMS_URL,
            commercial_use_permission=True,
            redistribution_permission=True,
            derivatives_permission=True,
            attribution_required=True,
            attribution_text="Credit: U.S. Geological Survey.",
            public_display_permission=True,
            raw_download_permission=True,
            terms_checked_at=date(2026, 7, 24),
            legal_review_status=LegalReviewStatus.NOT_REQUIRED,
        ),
    )


class USGSProperties(BaseModel):
    model_config = ConfigDict(extra="allow")

    mag: float
    place: str
    time: int
    updated: int
    url: str
    detail: str
    status: str
    net: str
    code: str
    magType: str
    type: str
    title: str


class USGSGeometry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["Point"]
    coordinates: list[float] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_ranges(self) -> USGSGeometry:
        longitude, latitude, depth = self.coordinates
        if (
            not all(math.isfinite(value) for value in (longitude, latitude, depth))
            or not -180 <= longitude <= 180
            or not -90 <= latitude <= 90
            or depth < 0
        ):
            raise ValueError("USGS point coordinates or depth are outside valid ranges.")
        return self


class USGSFeature(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["Feature"]
    id: str
    properties: USGSProperties
    geometry: USGSGeometry


class USGSFeatureCollection(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["FeatureCollection"]
    features: list[USGSFeature]


class USGSEarthquakeAdapter:
    metadata = SourceMetadata(
        slug=USGS_SOURCE_SLUG,
        name="USGS Earthquake Catalog",
        publisher="U.S. Geological Survey, Earthquake Hazards Program",
        canonical_url="https://earthquake.usgs.gov/fdsnws/event/1/",
        usage_notes=(
            "Official USGS public earthquake catalog data. Attribute the U.S. Geological "
            "Survey and retain the source record locator and retrieval metadata."
        ),
    )

    def retrieve(self, *, fixture_path: Path | None = None) -> bytes:
        if fixture_path is not None:
            return fixture_path.read_bytes()
        request = urllib.request.Request(
            USGS_QUERY_URL,
            headers={"User-Agent": "day-perspective-offline-ingestion/0.1"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload: bytes = response.read()
            return payload

    def validate(self, payload: bytes) -> USGSFeature:
        parsed = USGSFeatureCollection.model_validate_json(payload)
        matches = [feature for feature in parsed.features if feature.id == USGS_EVENT_ID]
        if len(matches) != 1:
            raise ValueError("The USGS release must contain exactly one golden source record.")
        record = matches[0]
        if record.properties.url != USGS_RECORD_LOCATOR:
            raise ValueError("The USGS record locator does not match the golden event.")
        return record

    def source_record_identity(self, record: USGSFeature) -> str:
        return record.id

    def record_to_claims(self, record: USGSFeature) -> tuple[ClaimDraft, ...]:
        longitude, latitude, depth = record.geometry.coordinates
        if record.properties.time % 1000 != 0:
            raise ValueError(
                "USGS timestamps with subsecond precision are not supported by "
                "the current claim and publication schema"
            )
        occurrence = datetime.fromtimestamp(record.properties.time / 1000, tz=UTC)
        local = occurrence.astimezone(ZoneInfo(ALASKA_TIMEZONE))
        offset = local.utcoffset()
        if offset is None:
            raise ValueError("Historical Alaska local offset could not be determined.")
        local_date = local.date()
        if local_date != GOLDEN_DATE:
            raise ValueError("USGS occurrence does not map to the expected Alaska civil date.")
        return (
            ClaimDraft("event_identity", record.id, {"event_id": record.id}),
            ClaimDraft("event_type", record.properties.type, {"type": record.properties.type}),
            ClaimDraft("event_title", record.properties.title, {"title": record.properties.title}),
            ClaimDraft(
                "occurrence_timestamp",
                occurrence.isoformat().replace("+00:00", "Z"),
                {"utc": occurrence.isoformat().replace("+00:00", "Z")},
                temporal_precision=TemporalPrecision.SECOND,
                date_role=DateRole.OCCURRED,
            ),
            ClaimDraft(
                "local_civil_date",
                local_date.isoformat(),
                {
                    "date": local_date.isoformat(),
                    "timezone": ALASKA_TIMEZONE,
                    "utc_offset_minutes": int(offset.total_seconds() / 60),
                },
                temporal_precision=TemporalPrecision.DAY,
                temporal_assignment=TemporalAssignment.INFERRED,
                date_role=DateRole.OCCURRED,
            ),
            ClaimDraft(
                "epicenter_coordinates",
                f"{latitude:.3f}, {longitude:.3f}",
                {"latitude": latitude, "longitude": longitude},
                unit="decimal degrees",
            ),
            ClaimDraft(
                "epicenter_geography",
                record.properties.place,
                {"display_name": record.properties.place, "region": "Alaska"},
            ),
            ClaimDraft(
                "magnitude",
                f"{record.properties.mag:g} {record.properties.magType.upper()}",
                {"value": record.properties.mag, "scale": record.properties.magType.lower()},
                unit=record.properties.magType.lower(),
                lower_bound=Decimal(str(record.properties.mag)),
                upper_bound=Decimal(str(record.properties.mag)),
            ),
            ClaimDraft(
                "depth",
                f"{depth:g} km",
                {"value": depth, "unit": "km"},
                unit="km",
                lower_bound=Decimal(str(depth)),
                upper_bound=Decimal(str(depth)),
            ),
        )


def _methodology(session: Session) -> Methodology:
    existing = session.scalar(
        select(Methodology).where(
            Methodology.slug == "usgs-authoritative-single-source",
            Methodology.version == "1",
        )
    )
    if existing is not None:
        return existing
    definition = {
        "authority": "USGS Earthquake Hazards Program",
        "resolution": "Accept one validated official catalog claim with explicit single-source consequence.",
        "local_date": "Convert UTC occurrence using IANA America/Anchorage historical rules.",
    }
    row = Methodology(
        slug="usgs-authoritative-single-source",
        version="1",
        name="USGS authoritative earthquake resolution",
        description=definition["resolution"],
        method_kind="deterministic_resolution_and_editorial_selection",
        formula=None,
        code_version="0.2.0",
        definition_hash=hashlib.sha256(canonical_json_bytes(definition)).hexdigest(),
        legal_review_status=LegalReviewStatus.NOT_REQUIRED,
    )
    session.add(row)
    session.flush()
    return row


def ingest_usgs(
    session: Session,
    *,
    adapter: USGSEarthquakeAdapter,
    raw_store: RawSourceStore,
    fixture_path: Path | None = None,
    dry_run: bool = False,
) -> IngestionResult:
    payload = adapter.retrieve(fixture_path=fixture_path)
    checksum = hashlib.sha256(payload).hexdigest()
    if dry_run:
        run = PipelineRun(
            pipeline_name="usgs-earthquake-adapter",
            code_version="0.3.0",
            configuration_hash=hashlib.sha256(
                canonical_json_bytes(
                    {
                        "dry_run": True,
                        "fixture": fixture_path is not None,
                        "query_url": USGS_QUERY_URL,
                    }
                )
            ).hexdigest(),
            status="running",
            details={
                "dry_run": True,
                "mode": "fixture" if fixture_path is not None else "live",
            },
        )
        session.add(run)
        session.flush()
        try:
            record = adapter.validate(payload)
            drafts = adapter.record_to_claims(record)
            record_hash = hashlib.sha256(
                canonical_json_bytes(record.model_dump(mode="json"))
            ).hexdigest()
            run.status = "succeeded"
            run.completed_at = datetime.now(UTC)
            session.add(
                QualityCheck(
                    pipeline_run_id=run.id,
                    check_name="usgs_dry_run_validation",
                    status="passed",
                    subject_type="pipeline_run",
                    subject_id=run.id,
                    details={"record_id": record.id, "claim_count": len(drafts)},
                )
            )
            return IngestionResult(
                run.id, None, (), checksum, record_hash, False, True
            )
        except Exception as error:
            run.status = "failed"
            run.completed_at = datetime.now(UTC)
            run.details = {**run.details, "error": type(error).__name__}
            session.add(
                QualityCheck(
                    pipeline_run_id=run.id,
                    check_name="usgs_dry_run_validation",
                    status="failed",
                    subject_type="pipeline_run",
                    subject_id=run.id,
                    details={"error": str(error)},
                )
            )
            session.flush()
            raise

    run = PipelineRun(
        pipeline_name="usgs-earthquake-adapter",
        code_version="0.2.0",
        configuration_hash=hashlib.sha256(
            canonical_json_bytes(
                {"fixture": fixture_path is not None, "query_url": USGS_QUERY_URL}
            )
        ).hexdigest(),
        status="running",
        details={"mode": "fixture" if fixture_path is not None else "live"},
    )
    session.add(run)
    session.flush()
    try:
        with session.begin_nested():
            record = adapter.validate(payload)
            drafts = adapter.record_to_claims(record)
            record_hash = hashlib.sha256(
                canonical_json_bytes(record.model_dump(mode="json"))
            ).hexdigest()
            source = session.scalar(select(Source).where(Source.slug == adapter.metadata.slug))
            if source is None:
                source = Source(
                    slug=adapter.metadata.slug,
                    name=adapter.metadata.name,
                    publisher=adapter.metadata.publisher,
                    canonical_url=adapter.metadata.canonical_url,
                    legal_review_status=LegalReviewStatus.NOT_REQUIRED,
                )
                session.add(source)
                session.flush()
            existing_release = session.scalar(
                select(SourceRelease).where(
                    SourceRelease.source_id == source.id,
                    SourceRelease.raw_checksum_sha256 == checksum,
                )
            )
            if existing_release is not None:
                _register_usgs_license(session, existing_release.id)
                existing_claim_ids = tuple(
                    session.scalars(
                        select(Claim.id)
                        .where(Claim.source_release_id == existing_release.id)
                        .order_by(Claim.claim_type)
                    )
                )
                run.status = "succeeded"
                run.completed_at = datetime.now(UTC)
                run.details = {**run.details, "idempotent": True}
                return IngestionResult(
                    run.id,
                    existing_release.id,
                    existing_claim_ids,
                    checksum,
                    record_hash,
                    True,
                    False,
                )
            storage_uri = raw_store.write(adapter.metadata.slug, checksum, payload)
            release = create_source_release(
                session,
                source_id=source.id,
                release_label=f"usgs-{record.id}-{checksum[:12]}",
                source_url=USGS_QUERY_URL,
                raw_storage_uri=storage_uri,
                raw_bytes=payload,
                raw_record_count=1,
                pipeline_run_id=run.id,
                metadata_json={
                    "dataset": "FDSN Event Web Service v1 GeoJSON",
                    "quality_contract_version": "1",
                    "required_quality_checks": [
                        "usgs_schema_and_golden_record"
                    ],
                    "retrieval_mode": "fixture" if fixture_path is not None else "live",
                    "record_locator": record.properties.url,
                    "usage_and_attribution": adapter.metadata.usage_notes,
                    "record_updated_epoch_ms": record.properties.updated,
                },
                legal_review_status=LegalReviewStatus.NOT_REQUIRED,
            )
            raw_record = RawSourceRecord(
                source_release_id=release.id,
                source_record_id=adapter.source_record_identity(record),
                source_record_locator=record.properties.url,
                raw_storage_uri=storage_uri,
                raw_checksum_sha256=checksum,
                schema_version="usgs-fdsn-geojson-v1",
                payload_json=record.model_dump(mode="json"),
            )
            session.add(raw_record)
            session.flush()
            _register_usgs_license(session, release.id)
            claims: list[Claim] = []
            for draft in drafts:
                claim = create_claim(
                    session,
                    source_release_id=release.id,
                    source_record_locator=record.properties.url,
                    source_record_hash_sha256=record_hash,
                    claim_type=draft.predicate,
                    assertion_text=draft.text,
                    assertion_json=draft.value,
                    assertion_status=ClaimAssertionStatus.CANDIDATE,
                    unit=draft.unit,
                    lower_bound=draft.lower_bound,
                    upper_bound=draft.upper_bound,
                )
                claim.pipeline_run_id = run.id
                claim.temporal_precision = draft.temporal_precision
                claim.temporal_assignment = draft.temporal_assignment
                claim.date_role = draft.date_role
                if draft.predicate == "local_civil_date":
                    claim.temporal_start = GOLDEN_DATE
                    claim.temporal_end = GOLDEN_DATE
                session.add(
                    ReviewTask(
                        claim_id=claim.id,
                        status="open",
                        priority="normal",
                        rationale=f"Review imported USGS {draft.predicate} claim.",
                    )
                )
                claims.append(claim)
            session.add(
                QualityCheck(
                    pipeline_run_id=run.id,
                    check_name="usgs_schema_and_golden_record",
                    status="passed",
                    subject_type="source_release",
                    subject_id=release.id,
                    details={"record_id": record.id, "claim_count": len(claims)},
                )
            )
            run.status = "succeeded"
            run.completed_at = datetime.now(UTC)
            run.details = {**run.details, "idempotent": False, "checksum": checksum}
            return IngestionResult(
                run.id,
                release.id,
                tuple(claim.id for claim in claims),
                checksum,
                record_hash,
                False,
                False,
            )
    except Exception as error:
        run.status = "failed"
        run.completed_at = datetime.now(UTC)
        run.details = {**run.details, "error": type(error).__name__}
        session.add(
            QualityCheck(
                pipeline_run_id=run.id,
                check_name="usgs_schema_and_golden_record",
                status="failed",
                subject_type="pipeline_run",
                details={"error": str(error)},
            )
        )
        session.flush()
        raise


@dataclass(frozen=True)
class ResolutionDecision:
    status: Literal["accepted", "unresolved"]
    supporting_indexes: tuple[int, ...]
    dissenting_indexes: tuple[int, ...]
    independent_source_count: int
    rationale: str


@dataclass(frozen=True)
class EvidenceCandidate:
    value: str | float
    authoritative: bool
    source_release_id: UUID


def deterministic_resolution(
    session: Session,
    candidates: tuple[EvidenceCandidate, ...],
    *,
    tolerance: float | None = None,
) -> ResolutionDecision:
    if not candidates:
        raise ValueError("Resolution requires at least one candidate.")
    authoritative = [index for index, item in enumerate(candidates) if item.authoritative]
    if len(candidates) == 1 and authoritative:
        return ResolutionDecision(
            "accepted",
            (0,),
            (),
            1,
            "Accepted one authoritative source; quality records the lack of independent corroboration.",
        )
    baseline = candidates[authoritative[0] if authoritative else 0].value
    supporting: list[int] = []
    dissenting: list[int] = []
    for index, candidate in enumerate(candidates):
        agrees = candidate.value == baseline
        if (
            not agrees
            and tolerance is not None
            and isinstance(candidate.value, int | float)
            and isinstance(baseline, int | float)
        ):
            agrees = abs(float(candidate.value) - float(baseline)) <= tolerance
        (supporting if agrees else dissenting).append(index)
    independent_roots: set[UUID] = set()
    for index in supporting:
        independent_roots.update(
            lineage_root_ids(session, candidates[index].source_release_id)
        )
    independent = len(independent_roots)
    if dissenting:
        return ResolutionDecision(
            "unresolved",
            tuple(supporting),
            tuple(dissenting),
            independent,
            "Unresolved disagreement remains outside a declared tolerance.",
        )
    return ResolutionDecision(
        "accepted",
        tuple(supporting),
        tuple(dissenting),
        independent,
        (
            "Accepted bounded agreement with dissent retained."
            if dissenting
            else "Accepted agreeing evidence; dependent lineage counted once."
        ),
    )


def derive_quality(*, independent_sources: int, complete_predicates: int) -> tuple[str, str, dict[str, str]]:
    dimensions = {
        "temporal_precision": "second",
        "geographic_coverage": "epicenter point and named Alaska region",
        "measurement_directness": "official catalog measurement",
        "source_agreement": "no dissent in this release",
        "source_independence": (
            "single authoritative source"
            if independent_sources == 1
            else f"{independent_sources} independent sources"
        ),
        "completeness": f"{complete_predicates}/9 supported predicates; casualty impact unavailable",
        "revision_stability": "immutable retrieved release; upstream record may be revised",
        "methodology_transparency": "deterministic rules and historical timezone conversion published",
    }
    if complete_predicates < 9:
        grade = "C"
        explanation = (
            "Grade C: the available official USGS evidence is incomplete for the "
            "required event predicates. The assessment retains the available "
            "precision without overstating coverage."
        )
    elif independent_sources <= 0:
        grade = "C"
        explanation = (
            "Grade C: all required event predicates are present, but no independent "
            "source supports publication."
        )
    elif independent_sources == 1:
        grade = "B"
        explanation = (
            "Grade B: the occurrence, time, epicenter, magnitude, and depth come "
            "from one validated official USGS catalog release with second-level "
            "and point-level detail. The grade is limited because this is "
            "single-source acceptance with no independent confirmation and does "
            "not assert a casualty value."
        )
    else:
        grade = "B"
        explanation = (
            "Grade B: all required event predicates are present with second-level "
            "and point-level detail, supported by "
            f"{independent_sources} independent sources."
        )
    return grade, explanation, dimensions


def accept_and_resolve_release(
    session: Session,
    source_release_id: UUID,
    *,
    review_candidates: bool = True,
) -> dict[str, ResolvedClaim]:
    release = session.get(SourceRelease, source_release_id)
    if release is None:
        raise ValueError("Unknown source release.")
    source = session.get(Source, release.source_id)
    raw_record = session.scalar(
        select(RawSourceRecord).where(
            RawSourceRecord.source_release_id == source_release_id,
            RawSourceRecord.source_record_id == USGS_EVENT_ID,
        )
    )
    if source is None or source.slug != USGS_SOURCE_SLUG or raw_record is None:
        raise ValueError(
            "The golden resolver requires the official USGS release and event record."
        )
    claims = list(
        session.scalars(
            select(Claim)
            .where(Claim.source_release_id == source_release_id)
            .order_by(Claim.claim_type)
        )
    )
    if len(claims) != 9:
        raise ValueError("The golden release must contain all nine predicate claims.")
    methodology = _methodology(session)
    resolved: dict[str, ResolvedClaim] = {}
    for claim in claims:
        if claim.assertion_status in {
            ClaimAssertionStatus.CANDIDATE,
            ClaimAssertionStatus.IN_REVIEW,
        }:
            if not review_candidates:
                raise ValueError(
                    "Resolution requires claims to be explicitly accepted first."
                )
            record_claim_review(
                session,
                claim=claim,
                decision=ReviewDecisionValue.ACCEPTED,
                rationale="Reviewed against the validated official USGS fixture.",
                reviewed_by="development-fixture-review",
            )
        elif claim.assertion_status != ClaimAssertionStatus.ACCEPTED:
            raise ValueError("Rejected, superseded, or retracted claims cannot be resolved.")
        prior = session.scalar(
            select(ResolvedClaim)
            .where(ResolvedClaim.canonical_key == f"usgs:{USGS_EVENT_ID}:{claim.claim_type}")
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
                canonical_key=f"usgs:{USGS_EVENT_ID}:{claim.claim_type}",
                resolved_value=claim.assertion_json or {"text": claim.assertion_text},
                rationale=(
                    "Accepted the validated official USGS catalog claim. This is single-source "
                    "acceptance, not independent corroboration."
                ),
                supporting_claim_ids=[claim.id],
                resolution_method=(
                    ResolutionMethod.METHODOLOGICAL_DERIVATION
                    if claim.claim_type == "local_civil_date"
                    else ResolutionMethod.SINGLE_SOURCE
                ),
                methodology_id=methodology.id,
                supersedes_resolved_claim_id=prior.id if prior is not None else None,
            )
            selected_resolution.comparability_status = (
                ComparabilityStatus.PARTIALLY_COMPARABLE
            )
        resolved[claim.claim_type] = selected_resolution
        record_editorial_selection(
            session,
            profile_date=GOLDEN_DATE,
            section_key="recorded_on_this_date",
            resolved_claim_id=selected_resolution.id,
            status=EditorialSelectionStatus.SELECTED,
            display_rank=len(resolved),
            rationale="Selected for the reviewed USGS golden-date recorded event.",
            reviewed_by="development-fixture-review",
        )
    tasks = list(
        session.scalars(
            select(ReviewTask).where(
                ReviewTask.claim_id.in_([claim.id for claim in claims]),
                ReviewTask.status.in_(("open", "in_progress")),
            )
        )
    )
    for task in tasks:
        task.status = "resolved"
        task.completed_at = datetime.now(UTC)

    identity = resolved["event_identity"]
    event = session.scalar(
        select(Event)
        .join(ResolvedClaim, Event.resolved_claim_id == ResolvedClaim.id)
        .where(ResolvedClaim.canonical_key == identity.canonical_key)
    )
    timestamp = datetime.fromisoformat(
        str(resolved["occurrence_timestamp"].resolved_value["utc"]).replace(
            "Z", "+00:00"
        )
    )
    local_value = resolved["local_civil_date"].resolved_value
    local_date = date.fromisoformat(str(local_value["date"]))
    timezone_name = str(local_value["timezone"])
    local_timestamp = timestamp.astimezone(ZoneInfo(timezone_name))
    display_label = (
        f"{local_date:%B} {local_date.day}, {local_date.year} at "
        f"{local_timestamp.hour % 12 or 12}:{local_timestamp:%M:%S} "
        f"{local_timestamp:%p} {timezone_name}"
    )
    if event is None:
        event = Event(
            resolved_claim_id=identity.id,
            event_type=str(resolved["event_type"].resolved_value["type"]),
            canonical_title=str(resolved["event_title"].resolved_value["title"]),
            summary="Official USGS catalog occurrence selected for the golden date.",
            data_status=DataStatus.REPORTED,
        )
        session.add(event)
        session.flush()
    else:
        event.resolved_claim_id = identity.id
        event.event_type = str(resolved["event_type"].resolved_value["type"])
        event.canonical_title = str(
            resolved["event_title"].resolved_value["title"]
        )
    event_time = session.scalar(
        select(EventTime).where(
            EventTime.event_id == event.id,
            EventTime.is_primary.is_(True),
        )
    )
    if event_time is None:
        event_time = EventTime(
            event_id=event.id,
            temporal_precision=TemporalPrecision.SECOND,
            temporal_assignment=TemporalAssignment.DIRECT_RECORD,
            date_role=DateRole.OCCURRED,
            is_primary=True,
        )
        session.add(event_time)
    event_time.provenance_resolved_claim_id = resolved["occurrence_timestamp"].id
    event_time.local_date_provenance_resolved_claim_id = resolved[
        "local_civil_date"
    ].id
    event_time.start_date = local_date
    event_time.end_date = local_date
    event_time.exact_timestamp = timestamp
    event_time.local_date = local_date
    event_time.timezone_name = timezone_name
    event_time.utc_offset_minutes = int(local_value["utc_offset_minutes"])
    event_time.interpretation = (
        f"The USGS UTC occurrence is assigned to {local_date.isoformat()} under "
        f"historical {timezone_name} civil-time rules."
    )
    event_time.display_label = display_label
    geography = session.scalar(
        select(Geography).where(Geography.stable_key == "us-ak")
    )
    if geography is None:
        geography = Geography(
            stable_key="us-ak", geography_kind="state_or_territory"
        )
        session.add(geography)
        session.flush()
    geography_version = session.scalar(
        select(GeographyVersion).where(
            GeographyVersion.geography_id == geography.id,
            GeographyVersion.valid_from == date(1959, 1, 3),
        )
    )
    if geography_version is None:
        geography_version = GeographyVersion(
            geography_id=geography.id,
            provenance_resolved_claim_id=resolved["epicenter_geography"].id,
            name=str(resolved["epicenter_geography"].resolved_value["region"]),
            identifier_code="US-AK",
            valid_from=date(1959, 1, 3),
            valid_to=None,
            boundary_geometry=None,
        )
        session.add(geography_version)
        session.flush()
    geography_version.provenance_resolved_claim_id = resolved[
        "epicenter_geography"
    ].id
    geography_version.name = str(
        resolved["epicenter_geography"].resolved_value["region"]
    )
    coordinates = resolved["epicenter_coordinates"].resolved_value
    event_location = session.scalar(
        select(EventLocation).where(
            EventLocation.event_id == event.id,
            EventLocation.location_role == "epicenter",
        )
    )
    if event_location is None:
        event_location = EventLocation(
            event_id=event.id,
            location_role="epicenter",
        )
        session.add(event_location)
    event_location.geography_version_id = geography_version.id
    event_location.provenance_resolved_claim_id = resolved[
        "epicenter_coordinates"
    ].id
    event_location.point_geometry = WKTElement(
        f"POINT({coordinates['longitude']} {coordinates['latitude']})",
        srid=4326,
    )
    event_location.display_label = str(
        resolved["epicenter_geography"].resolved_value["display_name"]
    )
    grade, explanation, dimensions = derive_quality(
        independent_sources=1, complete_predicates=len(claims)
    )
    existing_quality = session.scalar(
        select(QualityAssessment).where(
            QualityAssessment.source_release_id == release.id,
            QualityAssessment.assessment_kind == "public_event_evidence_quality_v1",
        )
    )
    if existing_quality is None:
        existing_quality = QualityAssessment(
                source_release_id=release.id,
                methodology_id=methodology.id,
                assessment_kind="public_event_evidence_quality_v1",
                score=Decimal("0.80"),
                findings=dimensions,
                public_grade=grade,
                public_explanation=explanation,
                legal_review_status=LegalReviewStatus.NOT_REQUIRED,
            )
        session.add(existing_quality)
    quality_value = {
        "quality_grade": grade,
        "explanation": explanation,
        "dimensions": dimensions,
        "missing_data": {
            "casualties": {
                "state": "unavailable",
                "reason": (
                    "No casualty value is asserted from this selected USGS catalog "
                    "record; missing does not mean zero."
                ),
            }
        },
    }
    quality_fingerprint = content_hash(
        {
            "methodology": {
                "slug": methodology.slug,
                "version": methodology.version,
            },
            "resolved_claims": [
                {
                    "id": str(row.id),
                    "canonical_key": row.canonical_key,
                    "version": row.version,
                }
                for row in sorted(resolved.values(), key=lambda item: item.canonical_key)
            ],
            "value": quality_value,
        }
    )
    quality_derived = session.scalar(
        select(DerivedValue).where(
            DerivedValue.methodology_id == methodology.id,
            DerivedValue.value_kind == "public_event_evidence_quality",
            DerivedValue.period_start == GOLDEN_DATE,
            DerivedValue.input_fingerprint == quality_fingerprint,
        )
    )
    if quality_derived is None:
        quality_derived = DerivedValue(
            methodology_id=methodology.id,
            value_kind="public_event_evidence_quality",
            period_start=GOLDEN_DATE,
            period_end=GOLDEN_DATE,
            temporal_assignment=TemporalAssignment.EDITORIAL_CONTEXT,
            value_json=quality_value,
            data_status=DataStatus.FINAL,
            comparability_status=ComparabilityStatus.NOT_COMPARABLE,
            input_fingerprint=quality_fingerprint,
            calculation_version="0.3.0",
        )
        session.add(quality_derived)
        session.flush()
        session.add_all(
            [
                DerivedValueInput(
                    derived_value_id=quality_derived.id,
                    resolved_claim_id=row.id,
                    input_role="supporting",
                )
                for row in sorted(resolved.values(), key=lambda item: item.canonical_key)
            ]
        )
    record_editorial_selection(
        session,
        profile_date=GOLDEN_DATE,
        section_key="evidence_notes",
        derived_value_id=quality_derived.id,
        status=EditorialSelectionStatus.SELECTED,
        display_rank=1,
        rationale="Selected as the transparent evidence-quality explanation.",
        reviewed_by="development-fixture-review",
    )
    session.flush()
    return resolved


def _public_provenance(
    session: Session,
    claim: Claim,
    resolved: ResolvedClaim,
    release: SourceRelease,
    source: Source,
    methodology: Methodology,
) -> dict[str, Any]:
    evidence_rows = list(
        session.execute(
            select(ResolvedClaimEvidence.stance, Claim)
            .join(Claim, Claim.id == ResolvedClaimEvidence.claim_id)
            .where(ResolvedClaimEvidence.resolved_claim_id == resolved.id)
            .order_by(ResolvedClaimEvidence.stance, Claim.id)
        )
    )

    def claim_summary(item: Claim) -> dict[str, Any]:
        return {
            "predicate": item.claim_type,
            "value": item.assertion_json,
            "source_record_locator": item.source_record_locator,
            "source_record_hash_sha256": item.source_record_hash_sha256,
        }

    return {
        "root_type": "resolved_claim",
        "published_statement": "This statement is selected for the recorded-event section.",
        "resolved_claim": {
            "canonical_key": resolved.canonical_key,
            "version": resolved.version,
            "method": resolved.resolution_method.value,
            "rationale": resolved.rationale,
        },
        "supporting_claims": [
            claim_summary(item) for stance, item in evidence_rows if stance == "supporting"
        ],
        "dissenting_claims": [
            claim_summary(item) for stance, item in evidence_rows if stance == "dissenting"
        ],
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


def _public_quality_provenance(
    *,
    quality_derived: DerivedValue,
    claims: dict[str, Claim],
    release: SourceRelease,
    source: Source,
    methodology: Methodology,
) -> dict[str, Any]:
    return {
        "root_type": "derived_value",
        "published_statement": (
            "This explanation is derived from the selected event evidence."
        ),
        "derived_value": {
            "kind": quality_derived.value_kind,
            "calculation_version": quality_derived.calculation_version,
            "value": quality_derived.value_json,
        },
        "supporting_claims": [
            {
                "predicate": claim.claim_type,
                "value": claim.assertion_json,
                "source_record_locator": claim.source_record_locator,
                "source_record_hash_sha256": claim.source_record_hash_sha256,
            }
            for claim in sorted(claims.values(), key=lambda item: item.claim_type)
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


def publish_golden_profile(
    session: Session,
    *,
    store: LocalFilesystemPublishedProfileStore,
    force_new_version: bool = False,
) -> Any:
    source = session.scalar(select(Source).where(Source.slug == USGS_SOURCE_SLUG))
    if source is None:
        raise ValueError("USGS fixture has not been ingested.")
    release = session.scalar(
        select(SourceRelease)
        .join(
            QualityCheck,
            QualityCheck.subject_id == SourceRelease.id,
        )
        .join(PipelineRun, QualityCheck.pipeline_run_id == PipelineRun.id)
        .where(
            SourceRelease.source_id == source.id,
            QualityCheck.subject_type == "source_release",
        )
        .order_by(PipelineRun.started_at.desc(), QualityCheck.id.desc())
    )
    if release is None:
        raise ValueError("USGS fixture has no source release.")
    resolved = reviewed_resolutions_for_release(session, release.id)
    methodology = _methodology(session)
    current_resolution_ids = {row.id for row in resolved.values()}
    quality_derived = None
    for candidate in session.scalars(
        select(DerivedValue)
        .where(
            DerivedValue.methodology_id == methodology.id,
            DerivedValue.value_kind == "public_event_evidence_quality",
            DerivedValue.period_start == GOLDEN_DATE,
        )
        .order_by(DerivedValue.created_at.desc())
    ):
        input_ids = set(
            session.scalars(
                select(DerivedValueInput.resolved_claim_id).where(
                    DerivedValueInput.derived_value_id == candidate.id
                )
            )
        )
        if input_ids == current_resolution_ids:
            quality_derived = candidate
            break
    if quality_derived is None:
        raise ValueError("A derived public quality explanation is required.")
    assert_release_publication_eligible(
        session,
        source_release_id=release.id,
        profile_date=GOLDEN_DATE,
        resolved_root_ids_by_section={
            "recorded_on_this_date": {row.id for row in resolved.values()}
        },
        derived_root_ids_by_section={"evidence_notes": {quality_derived.id}},
    )
    claims = {
        claim.claim_type: claim
        for claim in session.scalars(
            select(Claim).where(Claim.source_release_id == release.id)
        )
    }
    quality_value = quality_derived.value_json or {}
    quality_grade = quality_value.get("quality_grade")
    quality_explanation = quality_value.get("explanation")
    if not isinstance(quality_grade, str) or not isinstance(
        quality_explanation, str
    ):
        raise ValueError(
            "The selected derived quality root lacks a public grade or explanation."
        )
    un_context = build_un_wpp_profile_content(session)
    ucdp_context = build_ucdp_annual_profile_content(session)
    # Absent unless the comparison has been derived, which requires the whole
    # reference cohort. A profile without it simply makes no comparison.
    comparison = optional_conflict_comparison(
        session, year=GOLDEN_DATE.year, statement_index=0
    )
    resolved_values = {
        predicate: row.resolved_value for predicate, row in resolved.items()
    }
    occurrence = datetime.fromisoformat(
        str(resolved_values["occurrence_timestamp"]["utc"]).replace(
            "Z", "+00:00"
        )
    ).astimezone(UTC)
    local_value = resolved_values["local_civil_date"]
    local_date = date.fromisoformat(str(local_value["date"]))
    offset_minutes = int(local_value["utc_offset_minutes"])
    offset_sign = "+" if offset_minutes >= 0 else "-"
    offset_hours, offset_remainder = divmod(abs(offset_minutes), 60)
    offset_text = f"UTC{offset_sign}{offset_hours}"
    if offset_remainder:
        offset_text += f":{offset_remainder:02d}"

    definitions = [
        (
            "event-title",
            "event_title",
            str(resolved_values["event_title"]["title"]),
            resolved_values["event_title"],
        ),
        (
            "event-time-utc",
            "occurrence_timestamp",
            f"USGS records the occurrence at {occurrence:%Y-%m-%d %H:%M:%S} UTC.",
            resolved_values["occurrence_timestamp"],
        ),
        (
            "event-local-civil-date",
            "local_civil_date",
            (
                f"Historical {local_value['timezone']} civil-time rules assign "
                f"the occurrence to {local_date:%B} {local_date.day}, "
                f"{local_date.year} locally."
            ),
            {
                **local_value,
                "interpretation": (
                    "Methodological derivation using the IANA historical timezone "
                    f"rule; {offset_text} at the event instant."
                ),
            },
        ),
        (
            "event-magnitude",
            "magnitude",
            "USGS reports a magnitude of "
            f"{_display_number(resolved_values['magnitude']['value'])} "
            f"{str(resolved_values['magnitude']['scale']).upper()}.",
            resolved_values["magnitude"],
        ),
        (
            "event-depth",
            "depth",
            "USGS reports a depth of "
            f"{_display_number(resolved_values['depth']['value'])} "
            f"{resolved_values['depth']['unit']}.",
            resolved_values["depth"],
        ),
        (
            "event-geography",
            "epicenter_geography",
            "USGS names the location as "
            f"{resolved_values['epicenter_geography']['display_name']}.",
            resolved_values["epicenter_geography"],
        ),
        (
            "event-coordinates",
            "epicenter_coordinates",
            "USGS places the epicenter at "
            f"{_display_number(resolved_values['epicenter_coordinates']['latitude'])} "
            "latitude, "
            f"{_display_number(resolved_values['epicenter_coordinates']['longitude'])} "
            "longitude.",
            resolved_values["epicenter_coordinates"],
        ),
        (
            "event-type",
            "event_type",
            f"USGS classifies the record as an "
            f"{resolved_values['event_type']['type']}.",
            resolved_values["event_type"],
        ),
    ]
    statements: list[dict[str, Any]] = []
    evidence: list[PublicationStatementEvidenceInput] = []
    for index, (statement_id, predicate, text, details) in enumerate(definitions):
        claim = claims[predicate]
        resolved_claim = resolved[predicate]
        statements.append(
            {
                "statement_id": statement_id,
                "statement": text,
                "details": details,
                "provenance_note": "Official USGS catalog; single-source acceptance.",
                "provenance": _public_provenance(
                    session, claim, resolved_claim, release, source, methodology
                ),
            }
        )
        evidence.append(
            PublicationStatementEvidenceInput(
                statement_path=f"/sections/recorded_on_this_date/{index}",
                resolved_claim_id=resolved_claim.id,
            )
        )
    evidence_statement = {
        "statement_id": "quality-assessment",
        "statement": quality_explanation,
        "details": quality_derived.value_json,
        "provenance_note": "Quality methodology v1; no opaque weighted truth score.",
        "provenance": _public_quality_provenance(
            quality_derived=quality_derived,
            claims=claims,
            release=release,
            source=source,
            methodology=methodology,
        ),
    }
    sections = {
        "recorded_on_this_date": statements,
        "typical_day_in_this_year": un_context.typical_statements,
        "wider_historical_context": [
            *un_context.context_statements,
            *ucdp_context.statements,
        ],
        "curated_claims": [],
        # The archive's first app-derived claim. Published here rather than
        # on every date because derived_comparisons is an EDITORIAL_SECTION:
        # populating it archive-wide would flip 27,759 context_only profiles
        # to partially_enriched, telling readers those pages offer curated
        # content they do not. This date is already `enriched`, so
        # carrying the comparison changes no tier. #62 holds the
        # archive-wide question; D039 records the reasoning.
        "derived_comparisons": list(comparison.statements) if comparison else [],
        "wonder_and_progress": [],
        "evidence_notes": [evidence_statement],
    }
    evidence.extend(un_context.evidence)
    evidence.extend(ucdp_context.evidence)
    if comparison is not None:
        evidence.extend(comparison.evidence)
    payload = {
        "schema_version": "1",
        "date": GOLDEN_DATE.isoformat(),
        "profile_type": ProfileType.STANDARD_STATISTICAL.value,
        "sections": sections,
        "section_states": {
            key: (
                {"status": "available"}
                if key
                in (
                    {
                        "recorded_on_this_date",
                        "typical_day_in_this_year",
                        "wider_historical_context",
                        "evidence_notes",
                    }
                    | ({"derived_comparisons"} if comparison else set())
                )
                else {
                    "status": "not_yet_supported",
                    "reason": "This vertical slice does not publish this evidence class.",
                }
            )
            for key in sections
        },
        "quality": {
            "grade": quality_grade,
            "explanation": quality_explanation,
        },
        "source_attribution": {
            "name": source.name,
            "publisher": source.publisher,
            "url": USGS_RECORD_LOCATOR,
        },
    }
    evidence.append(
        PublicationStatementEvidenceInput(
            statement_path="/sections/evidence_notes/0",
            derived_value_id=quality_derived.id,
        )
    )
    # Only a published manifest can be superseded: an abandoned (withdrawn)
    # manifest has no day profile, and offering it as a predecessor would
    # permanently block republication after reconciliation.
    previous_manifest = session.scalar(
        select(PublicationManifest)
        .where(
            PublicationManifest.profile_date == GOLDEN_DATE,
            PublicationManifest.status == PublicationStatus.PUBLISHED,
        )
        .order_by(PublicationManifest.version.desc())
    )
    from app.models import DayProfile

    previous_profile = (
        session.scalar(
            select(DayProfile).where(
                DayProfile.publication_manifest_id == previous_manifest.id
            )
        )
        if previous_manifest is not None
        else None
    )
    # The recorded section this publishes is one canonical event, and the
    # version has to say so: a successor that omits the binding would leave the
    # admitted set to be inferred, and a co-published event would drop out of
    # the collision guard.
    golden_event = session.scalar(
        select(Event).where(
            Event.resolved_claim_id == resolved["event_identity"].id
        )
    )
    if golden_event is None:
        raise ValueError(
            "The golden recorded event has not been resolved into an Event."
        )
    # This publisher rebuilds only its own recorded statements, so on a date that
    # has since admitted another publisher's event it would mint a successor whose
    # payload and binding both omit it -- and a non-empty binding is believed, so
    # that event would simply stop existing for the collision guard. Carrying
    # another source's statements is not this slice's job; refusing is
    # recoverable, forgetting is not.
    published = session.scalar(
        select(PublicationManifest)
        .where(
            PublicationManifest.profile_date == GOLDEN_DATE,
            PublicationManifest.profile_type == ProfileType.STANDARD_STATISTICAL,
            PublicationManifest.status == PublicationStatus.PUBLISHED,
        )
        .order_by(PublicationManifest.version.desc())
    )
    if published is not None:
        co_published = events_behind_manifest(
            session, manifest=published
        ) - {golden_event.id}
        if co_published:
            raise ValueError(
                f"{GOLDEN_DATE.isoformat()} also publishes recorded events "
                f"{sorted(str(event_id) for event_id in co_published)}, which "
                "this publisher cannot carry. Republish through the publisher "
                "that owns the date's full admitted set rather than mint a "
                "version that forgets them."
            )
    return publish_day_profile(
        session,
        store=store,
        profile_date=GOLDEN_DATE,
        profile_type=ProfileType.STANDARD_STATISTICAL,
        payload=payload,
        statement_evidence=evidence,
        recorded_events=[
            RecordedEventBinding(
                event_id=golden_event.id,
                is_featured=True,
                featured_selection_id=None,
                statement_count=len(statements),
            )
        ],
        supersedes_manifest_id=previous_manifest.id if previous_manifest is not None else None,
        supersedes_day_profile_id=previous_profile.id if previous_profile is not None else None,
        methodology_id=methodology.id,
        editorial_revision=(previous_manifest.editorial_revision + 1 if previous_manifest else 1),
        force_new_version=force_new_version,
        manifest_metadata={
            "source_release_ids": [
                str(release.id),
                str(un_context.source_release_id),
                str(ucdp_context.source_release_id),
            ],
            "methodology_versions": [
                {"slug": methodology.slug, "version": methodology.version},
                {
                    "slug": un_context.methodology.slug,
                    "version": un_context.methodology.version,
                },
                {
                    "slug": ucdp_context.methodology.slug,
                    "version": ucdp_context.methodology.version,
                },
            ],
            "resolved_claim_versions": [
                {
                    "canonical_key": item.canonical_key,
                    "version": item.version,
                }
                for item in sorted(
                    [
                        *resolved.values(),
                        *un_context.resolved_claims,
                        *ucdp_context.resolved_claims,
                    ],
                    key=lambda row: row.canonical_key,
                )
            ],
            "editorial_revision": (
                previous_manifest.editorial_revision + 1 if previous_manifest else 1
            ),
        },
    )
