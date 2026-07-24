from __future__ import annotations

import hashlib
import os
import tempfile
import urllib.request
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from geoalchemy2.elements import WKTElement
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Claim,
    ClaimAssertionStatus,
    ComparabilityStatus,
    DataStatus,
    DateRole,
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
    canonical_json_bytes,
    create_claim,
    create_source_release,
    publish_day_profile,
    resolve_claim,
)

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
        if not -180 <= longitude <= 180 or not -90 <= latitude <= 90 or depth < 0:
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


@dataclass(frozen=True)
class SourceMetadata:
    slug: str
    name: str
    publisher: str
    canonical_url: str
    usage_notes: str


@dataclass(frozen=True)
class ClaimDraft:
    predicate: str
    text: str
    value: dict[str, Any]
    temporal_precision: TemporalPrecision = TemporalPrecision.UNKNOWN
    temporal_assignment: TemporalAssignment = TemporalAssignment.DIRECT_RECORD
    date_role: DateRole | None = None
    unit: str | None = None
    lower_bound: Decimal | None = None
    upper_bound: Decimal | None = None


@dataclass(frozen=True)
class IngestionResult:
    pipeline_run_id: UUID | None
    source_release_id: UUID | None
    claim_ids: tuple[UUID, ...]
    checksum: str
    idempotent: bool
    dry_run: bool


class RawSourceStore(Protocol):
    def write(self, source_slug: str, checksum: str, payload: bytes) -> str: ...

    def read(self, storage_uri: str, expected_checksum: str) -> bytes: ...


class LocalFilesystemRawSourceStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def write(self, source_slug: str, checksum: str, payload: bytes) -> str:
        relative = Path(source_slug) / f"{checksum}.json"
        destination = (self.root / relative).resolve()
        if not destination.is_relative_to(self.root):
            raise RuntimeError("Refused raw-source write outside configured storage.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            self.read(relative.as_posix(), checksum)
            return relative.as_posix()
        descriptor, temporary_path = tempfile.mkstemp(prefix=".raw-", dir=destination.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary_path, destination)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)
        return relative.as_posix()

    def read(self, storage_uri: str, expected_checksum: str) -> bytes:
        candidate = (self.root / storage_uri).resolve()
        if not candidate.is_relative_to(self.root):
            raise RuntimeError("Refused raw-source read outside configured storage.")
        payload = candidate.read_bytes()
        if hashlib.sha256(payload).hexdigest() != expected_checksum:
            raise RuntimeError("Raw source content did not match its release checksum.")
        return payload


class SourceAdapter(Protocol):
    metadata: SourceMetadata

    def retrieve(self, *, fixture_path: Path | None = None) -> bytes: ...

    def validate(self, payload: bytes) -> USGSFeature: ...

    def source_record_identity(self, record: USGSFeature) -> str: ...

    def record_to_claims(self, record: USGSFeature) -> tuple[ClaimDraft, ...]: ...


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
            return response.read()

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
    run = PipelineRun(
        pipeline_name="usgs-earthquake-adapter",
        code_version="0.2.0",
        configuration_hash=hashlib.sha256(
            canonical_json_bytes(
                {"fixture": fixture_path is not None, "query_url": USGS_QUERY_URL}
            )
        ).hexdigest(),
        status="running",
        started_at=datetime.now(UTC),
        details={"mode": "fixture" if fixture_path is not None else "live"},
    )
    session.add(run)
    session.flush()
    try:
        payload = adapter.retrieve(fixture_path=fixture_path)
        checksum = hashlib.sha256(payload).hexdigest()
        if dry_run:
            record = adapter.validate(payload)
            adapter.record_to_claims(record)
            run.status = "succeeded"
            run.completed_at = datetime.now(UTC)
            run.details = {**run.details, "dry_run": True, "checksum": checksum}
            session.add(
                QualityCheck(
                    pipeline_run_id=run.id,
                    check_name="usgs_schema_and_golden_record",
                    status="passed",
                    subject_type="pipeline_run",
                    subject_id=run.id,
                    details={"record_id": record.id, "dry_run": True},
                )
            )
            return IngestionResult(run.id, None, (), checksum, False, True)

        with session.begin_nested():
            record = adapter.validate(payload)
            drafts = adapter.record_to_claims(record)
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
            claims: list[Claim] = []
            for draft in drafts:
                claim = create_claim(
                    session,
                    source_release_id=release.id,
                    source_record_locator=record.properties.url,
                    source_record_hash_sha256=checksum,
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
    lineage_root: str


def deterministic_resolution(
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
    independent = len({candidates[index].lineage_root for index in supporting})
    if dissenting and tolerance is None:
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
    grade = "B" if independent_sources == 1 and complete_predicates >= 9 else "C"
    explanation = (
        "Grade B: the occurrence, time, epicenter, magnitude, and depth come from one "
        "validated official USGS catalog release with second-level and point-level detail. "
        "The grade is limited because this is single-source acceptance with no independent "
        "confirmation and "
        "does not assert a casualty value."
    )
    return grade, explanation, dimensions


def accept_and_resolve_release(session: Session, source_release_id: UUID) -> dict[str, ResolvedClaim]:
    release = session.get(SourceRelease, source_release_id)
    if release is None:
        raise ValueError("Unknown source release.")
    claims = list(
        session.scalars(
            select(Claim)
            .where(Claim.source_release_id == source_release_id)
            .order_by(Claim.claim_type)
        )
    )
    if len(claims) != 9:
        raise ValueError("The golden release must contain all nine predicate claims.")
    allowed_statuses = {
        ClaimAssertionStatus.CANDIDATE,
        ClaimAssertionStatus.IN_REVIEW,
        ClaimAssertionStatus.ACCEPTED,
    }
    blocked = [
        f"{claim.claim_type}={claim.assertion_status.value}"
        for claim in claims
        if claim.assertion_status not in allowed_statuses
    ]
    if blocked:
        raise ValueError(
            "Non-reviewable claims block resolution: " + ", ".join(sorted(blocked))
        )
    methodology = _methodology(session)
    resolved: dict[str, ResolvedClaim] = {}
    for claim in claims:
        if claim.assertion_status != ClaimAssertionStatus.ACCEPTED:
            claim.assertion_status = ClaimAssertionStatus.ACCEPTED
        prior = session.scalar(
            select(ResolvedClaim)
            .where(ResolvedClaim.canonical_key == f"usgs:{USGS_EVENT_ID}:{claim.claim_type}")
            .order_by(ResolvedClaim.version.desc())
        )
        if prior is not None and prior.resolved_value == claim.assertion_json:
            already_supports_current_claim = session.scalar(
                select(ResolvedClaimEvidence.claim_id).where(
                    ResolvedClaimEvidence.resolved_claim_id == prior.id,
                    ResolvedClaimEvidence.claim_id == claim.id,
                    ResolvedClaimEvidence.stance == "supporting",
                )
            )
            if already_supports_current_claim is not None:
                resolved[claim.claim_type] = prior
                continue
        row = resolve_claim(
            session,
            canonical_key=f"usgs:{USGS_EVENT_ID}:{claim.claim_type}",
            resolved_value=claim.assertion_json or {"text": claim.assertion_text},
            rationale=(
                "Accepted the validated official USGS catalog claim. This is single-source "
                "acceptance, not independent corroboration."
            ),
            supporting_claim_ids=[claim.id],
            resolution_method=ResolutionMethod.SINGLE_SOURCE,
            methodology_id=methodology.id,
            supersedes_resolved_claim_id=prior.id if prior is not None else None,
        )
        row.comparability_status = ComparabilityStatus.PARTIALLY_COMPARABLE
        resolved[claim.claim_type] = row
    tasks = list(
        session.scalars(
            select(ReviewTask).where(
                ReviewTask.claim_id.in_([claim.id for claim in claims]),
                ReviewTask.status == "open",
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
        .limit(1)
    )
    if event is None:
        event = Event(
            resolved_claim_id=identity.id,
            event_type="earthquake",
            canonical_title=str(resolved["event_title"].resolved_value["title"]),
            summary="Official USGS catalog occurrence selected for the golden date.",
            data_status=DataStatus.REPORTED,
        )
        session.add(event)
        session.flush()
    else:
        event.resolved_claim_id = identity.id
        event.canonical_title = str(resolved["event_title"].resolved_value["title"])
        event.data_status = DataStatus.REPORTED

    timestamp = datetime.fromisoformat(
        str(resolved["occurrence_timestamp"].resolved_value["utc"]).replace("Z", "+00:00")
    )
    local_value = resolved["local_civil_date"].resolved_value
    local_date = date.fromisoformat(str(local_value["date"]))
    timezone_name = str(local_value["timezone"])
    local_timestamp = timestamp.astimezone(ZoneInfo(timezone_name))
    event_time = session.scalar(
        select(EventTime).where(EventTime.event_id == event.id, EventTime.is_primary.is_(True))
    )
    if event_time is None:
        event_time = EventTime(event_id=event.id)
        session.add(event_time)
    event_time.provenance_resolved_claim_id = resolved["occurrence_timestamp"].id
    event_time.start_date = local_date
    event_time.end_date = local_date
    event_time.exact_timestamp = timestamp
    event_time.local_date = local_date
    event_time.timezone_name = timezone_name
    event_time.utc_offset_minutes = int(local_value["utc_offset_minutes"])
    event_time.interpretation = (
        f"The USGS UTC occurrence falls on {local_date.isoformat()} under historical "
        f"{timezone_name} civil-time rules "
        f"(UTC offset {int(local_value['utc_offset_minutes'])} minutes at this instant)."
    )
    event_time.temporal_precision = TemporalPrecision.SECOND
    event_time.temporal_assignment = TemporalAssignment.DIRECT_RECORD
    event_time.date_role = DateRole.OCCURRED
    event_time.is_primary = True
    event_time.display_label = local_timestamp.strftime("%B %d, %Y at %I:%M:%S %p %Z")

    geography = session.scalar(
        select(Geography).where(Geography.stable_key == "us-ak")
    )
    if geography is None:
        geography = Geography(stable_key="us-ak", geography_kind="state_or_territory")
        session.add(geography)
        session.flush()
    geography_version = session.scalar(
        select(GeographyVersion).where(
            GeographyVersion.geography_id == geography.id,
            GeographyVersion.identifier_code == "US-AK",
            GeographyVersion.valid_from == date(1959, 1, 3),
        )
    )
    if geography_version is None:
        geography_version = GeographyVersion(
            geography_id=geography.id,
            provenance_resolved_claim_id=resolved["epicenter_geography"].id,
            name="Alaska",
            identifier_code="US-AK",
            valid_from=date(1959, 1, 3),
            valid_to=None,
            boundary_geometry=None,
        )
        session.add(geography_version)
        session.flush()
    geography_version.provenance_resolved_claim_id = resolved["epicenter_geography"].id

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
        session.add(
            event_location
        )
    event_location.geography_version_id = geography_version.id
    event_location.provenance_resolved_claim_id = resolved["epicenter_coordinates"].id
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
        session.add(
            QualityAssessment(
                source_release_id=release.id,
                methodology_id=methodology.id,
                assessment_kind="public_event_evidence_quality_v1",
                score=Decimal("0.80"),
                findings=dimensions,
                public_grade=grade,
                public_explanation=explanation,
                legal_review_status=LegalReviewStatus.NOT_REQUIRED,
            )
        )
    session.flush()
    return resolved


def _public_provenance(
    claim: Claim,
    resolved: ResolvedClaim,
    release: SourceRelease,
    source: Source,
    methodology: Methodology,
) -> dict[str, Any]:
    return {
        "published_statement": "This statement is selected for the recorded-event section.",
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


def publish_golden_profile(
    session: Session,
    *,
    store: LocalFilesystemPublishedProfileStore,
) -> Any:
    source = session.scalar(select(Source).where(Source.slug == USGS_SOURCE_SLUG))
    if source is None:
        raise ValueError("USGS fixture has not been ingested.")
    release = session.scalar(
        select(SourceRelease)
        .join(PipelineRun, SourceRelease.pipeline_run_id == PipelineRun.id)
        .where(SourceRelease.source_id == source.id)
        .order_by(PipelineRun.started_at.desc(), SourceRelease.id.desc())
    )
    if release is None:
        raise ValueError("USGS fixture has no source release.")
    failed_check = session.scalar(
        select(QualityCheck).where(
            QualityCheck.pipeline_run_id == release.pipeline_run_id,
            QualityCheck.status == "failed",
        )
    )
    if failed_check is not None:
        raise ValueError("A failed ingestion quality check blocks publication.")
    resolved = accept_and_resolve_release(session, release.id)
    methodology = _methodology(session)
    claims = {
        claim.claim_type: claim
        for claim in session.scalars(
            select(Claim).where(Claim.source_release_id == release.id)
        )
    }
    quality = session.scalar(
        select(QualityAssessment).where(
            QualityAssessment.source_release_id == release.id,
            QualityAssessment.assessment_kind == "public_event_evidence_quality_v1",
        )
    )
    if quality is None or quality.public_grade is None or quality.public_explanation is None:
        raise ValueError("A public quality assessment is required before publication.")
    magnitude = claims["magnitude"].assertion_json or {}
    magnitude_value = float(magnitude["value"])
    magnitude_scale = str(magnitude["scale"])
    magnitude_display = f"{magnitude_value:g} {magnitude_scale.title()}"
    depth = claims["depth"].assertion_json or {}
    depth_value = float(depth["value"])
    depth_unit = str(depth["unit"])
    depth_display = f"{depth_value:g} {depth_unit}"
    occurrence = claims["occurrence_timestamp"].assertion_json or {}
    occurrence_timestamp = datetime.fromisoformat(
        str(occurrence["utc"]).replace("Z", "+00:00")
    )
    local = claims["local_civil_date"].assertion_json or {}
    local_date = date.fromisoformat(str(local["date"]))
    if local_date != GOLDEN_DATE:
        raise ValueError("The selected USGS release no longer belongs to the golden date.")
    event_title = str(claims["event_title"].assertion_text)
    geography_display = str(claims["epicenter_geography"].assertion_text)

    definitions = [
        (
            "event-identity",
            "event_identity",
            f"{event_title} is recorded by the official USGS catalog.",
            {"event_type": "earthquake", "title": event_title},
        ),
        (
            "event-time-utc",
            "occurrence_timestamp",
            f"USGS records the occurrence at "
            f"{occurrence_timestamp.strftime('%Y-%m-%d %H:%M:%S')} UTC.",
            {
                "utc": occurrence["utc"],
            },
        ),
        (
            "event-local-civil-date",
            "local_civil_date",
            (
                f"Historical {local['timezone']} civil-time rules assign the "
                f"occurrence to {local_date.strftime('%B %d, %Y')} locally "
                f"(UTC offset {int(local['utc_offset_minutes'])} minutes)."
            ),
            {
                **local,
                "local_date": local_date.isoformat(),
                "interpretation": (
                    "Historical IANA civil-time assignment from the separately "
                    "resolved local-date claim."
                ),
            },
        ),
        (
            "event-location",
            "epicenter_coordinates",
            f"USGS locates the epicenter at {geography_display}.",
            {
                **(claims["epicenter_coordinates"].assertion_json or {}),
                "display_name": geography_display,
            },
        ),
        (
            "event-magnitude",
            "magnitude",
            f"USGS reports a magnitude of {magnitude_display}.",
            magnitude,
        ),
        (
            "event-depth",
            "depth",
            f"USGS reports a depth of {depth_display}.",
            depth,
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
                    claim, resolved_claim, release, source, methodology
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
        "statement": quality.public_explanation,
        "details": {
            "quality_grade": quality.public_grade,
            "dimensions": quality.findings,
            "missing_data": {
                "casualties": {
                    "state": "unavailable",
                    "reason": (
                        "No casualty value is asserted from this selected USGS catalog record; "
                        "missing does not mean zero."
                    ),
                }
            },
        },
        "provenance_note": "Quality methodology v1; no opaque weighted truth score.",
        "provenance": _public_provenance(
            claims["event_identity"],
            resolved["event_identity"],
            release,
            source,
            methodology,
        ),
    }
    sections = {
        "recorded_on_this_date": statements,
        "typical_day_in_this_year": [],
        "wider_historical_context": [],
        "curated_claims": [],
        "derived_comparisons": [],
        "wonder_and_progress": [],
        "evidence_notes": [evidence_statement],
    }
    payload = {
        "schema_version": "1",
        "date": GOLDEN_DATE.isoformat(),
        "profile_type": ProfileType.STANDARD_STATISTICAL.value,
        "sections": sections,
        "section_states": {
            key: (
                {"status": "available"}
                if key in {"recorded_on_this_date", "evidence_notes"}
                else {
                    "status": "not_yet_supported",
                    "reason": "This vertical slice does not publish this evidence class.",
                }
            )
            for key in sections
        },
        "quality": {
            "grade": quality.public_grade,
            "explanation": quality.public_explanation,
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
            resolved_claim_id=resolved["event_identity"].id,
        )
    )
    previous_manifest = session.scalar(
        select(PublicationManifest)
        .where(PublicationManifest.profile_date == GOLDEN_DATE)
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
    return publish_day_profile(
        session,
        store=store,
        profile_date=GOLDEN_DATE,
        profile_type=ProfileType.STANDARD_STATISTICAL,
        payload=payload,
        statement_evidence=evidence,
        supersedes_manifest_id=previous_manifest.id if previous_manifest is not None else None,
        supersedes_day_profile_id=previous_profile.id if previous_profile is not None else None,
        methodology_id=methodology.id,
        editorial_revision=(previous_manifest.editorial_revision + 1 if previous_manifest else 1),
        manifest_metadata={
            "source_release_ids": [str(release.id)],
            "methodology_versions": [
                {"slug": methodology.slug, "version": methodology.version}
            ],
            "resolved_claim_versions": [
                {
                    "canonical_key": item.canonical_key,
                    "version": item.version,
                }
                for item in sorted(resolved.values(), key=lambda row: row.canonical_key)
            ],
            "editorial_revision": (
                previous_manifest.editorial_revision + 1 if previous_manifest else 1
            ),
        },
    )
