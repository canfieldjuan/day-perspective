from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def enum_type(enum_class: type[Enum], name: str) -> SAEnum:
    return SAEnum(
        enum_class,
        name=name,
        values_callable=lambda values: [str(value.value) for value in values],
    )


class ClaimAssertionStatus(str, Enum):
    IMPORTED = "imported"
    CANDIDATE = "candidate"
    IN_REVIEW = "in_review"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"


class TemporalPrecision(str, Enum):
    SECOND = "second"
    DAY = "day"
    MONTH = "month"
    YEAR = "year"
    DECADE = "decade"
    INTERVAL = "interval"
    UNKNOWN = "unknown"


class TemporalAssignment(str, Enum):
    DIRECT_RECORD = "direct_record"
    REPORTED = "reported"
    INFERRED = "inferred"
    PERIOD_CONTEXT = "period_context"
    UNIFORM_PERIOD_ALLOCATION = "uniform_period_allocation"
    MODELED_PERIOD_ALLOCATION = "modeled_period_allocation"
    EDITORIAL_CONTEXT = "editorial_context"
    UNKNOWN = "unknown"


class DateRole(str, Enum):
    OCCURRED = "occurred"
    BEGAN = "began"
    ENDED = "ended"
    REPORTED = "reported"
    DISCOVERED = "discovered"
    PUBLISHED = "published"
    PREDICTED = "predicted"
    COMMEMORATED = "commemorated"


class DataStatus(str, Enum):
    REPORTED = "reported"
    ESTIMATED = "estimated"
    MODELED = "modeled"
    PROVISIONAL = "provisional"
    FINAL = "final"
    MISSING = "missing"
    WITHDRAWN = "withdrawn"


class MissingReason(str, Enum):
    NOT_COLLECTED = "not_collected"
    NOT_AVAILABLE = "not_available"
    NOT_APPLICABLE = "not_applicable"
    WITHHELD = "withheld"
    INVALID = "invalid"
    UNKNOWN = "unknown"


class ResolutionMethod(str, Enum):
    SINGLE_SOURCE = "single_source"
    CORROBORATED = "corroborated"
    EDITORIAL_REVIEW = "editorial_review"
    METHODOLOGICAL_DERIVATION = "methodological_derivation"


class SourceLineageRelationship(str, Enum):
    REPUBLISHED = "republished"
    TRANSCRIBED = "transcribed"
    EXTRACTED = "extracted"
    AGGREGATED = "aggregated"
    DERIVED = "derived"


class ComparabilityStatus(str, Enum):
    COMPARABLE = "comparable"
    PARTIALLY_COMPARABLE = "partially_comparable"
    NOT_COMPARABLE = "not_comparable"
    UNKNOWN = "unknown"


class ImpactDirectness(str, Enum):
    DIRECT = "direct"
    INDIRECT = "indirect"
    MODELED = "modeled"
    CONTEXTUAL = "contextual"


class PublicationStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"
    WITHDRAWN = "withdrawn"


class ProfileType(str, Enum):
    LIMITED_HISTORICAL = "limited_historical"
    STANDARD_STATISTICAL = "standard_statistical"
    ENHANCED_STRUCTURED = "enhanced_structured"


class LegalReviewStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    RESTRICTED = "restricted"
    REJECTED = "rejected"


PUBLIC_DATE_MIN = date(1900, 1, 1)
PUBLIC_DATE_MAX = date(2025, 12, 31)


def profile_type_for_date(value: date) -> ProfileType | None:
    if value < PUBLIC_DATE_MIN or value > PUBLIC_DATE_MAX:
        return None
    if value.year <= 1949:
        return ProfileType.LIMITED_HISTORICAL
    if value.year <= 1988:
        return ProfileType.STANDARD_STATISTICAL
    return ProfileType.ENHANCED_STRUCTURED


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pipeline_name: Mapped[str] = mapped_column(String(160))
    code_version: Mapped[str] = mapped_column(String(160))
    configuration_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Methodology(Base):
    __tablename__ = "methodologies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(160))
    version: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    method_kind: Mapped[str] = mapped_column(String(80), default="editorial_or_calculation")
    formula: Mapped[str | None] = mapped_column(Text)
    code_version: Mapped[str] = mapped_column(String(160))
    definition_hash: Mapped[str] = mapped_column(String(64))
    legal_review_status: Mapped[LegalReviewStatus] = mapped_column(
        enum_type(LegalReviewStatus, "legal_review_status"), default=LegalReviewStatus.NOT_REQUIRED
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(160), unique=True)
    name: Mapped[str] = mapped_column(Text)
    publisher: Mapped[str | None] = mapped_column(Text)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    legal_review_status: Mapped[LegalReviewStatus] = mapped_column(
        enum_type(LegalReviewStatus, "legal_review_status"), default=LegalReviewStatus.PENDING
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    releases: Mapped[list[SourceRelease]] = relationship(back_populates="source")


class SourceRelease(Base):
    __tablename__ = "source_releases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="RESTRICT"))
    release_label: Mapped[str] = mapped_column(String(240))
    source_url: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    raw_storage_uri: Mapped[str] = mapped_column(Text)
    raw_checksum_sha256: Mapped[str] = mapped_column(String(64))
    raw_record_count: Mapped[int] = mapped_column(Integer)
    legal_review_status: Mapped[LegalReviewStatus] = mapped_column(
        enum_type(LegalReviewStatus, "legal_review_status"), default=LegalReviewStatus.PENDING
    )
    pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="RESTRICT")
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    source: Mapped[Source] = relationship(back_populates="releases")
    claims: Mapped[list[Claim]] = relationship(back_populates="source_release")
    raw_records: Mapped[list[RawSourceRecord]] = relationship(back_populates="source_release")


class RawSourceRecord(Base):
    __tablename__ = "raw_source_records"
    __table_args__ = (
        UniqueConstraint(
            "source_release_id",
            "source_record_id",
            name="raw_source_records_release_record_key",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_release_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_releases.id", ondelete="RESTRICT")
    )
    source_record_id: Mapped[str] = mapped_column(String(240))
    source_record_locator: Mapped[str] = mapped_column(Text)
    raw_storage_uri: Mapped[str] = mapped_column(Text)
    raw_checksum_sha256: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(80))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source_release: Mapped[SourceRelease] = relationship(back_populates="raw_records")


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_release_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_releases.id", ondelete="RESTRICT")
    )
    source_record_locator: Mapped[str] = mapped_column(Text)
    source_record_hash_sha256: Mapped[str] = mapped_column(String(64))
    assertion_status: Mapped[ClaimAssertionStatus] = mapped_column(
        enum_type(ClaimAssertionStatus, "claim_assertion_status"), default=ClaimAssertionStatus.IMPORTED
    )
    claim_type: Mapped[str] = mapped_column(String(120))
    assertion_text: Mapped[str | None] = mapped_column(Text)
    assertion_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    unit: Mapped[str | None] = mapped_column(Text)
    lower_bound: Mapped[Decimal | None] = mapped_column(Numeric)
    upper_bound: Mapped[Decimal | None] = mapped_column(Numeric)
    temporal_start: Mapped[date | None] = mapped_column(Date)
    temporal_end: Mapped[date | None] = mapped_column(Date)
    temporal_precision: Mapped[TemporalPrecision] = mapped_column(
        enum_type(TemporalPrecision, "temporal_precision"), default=TemporalPrecision.UNKNOWN
    )
    temporal_assignment: Mapped[TemporalAssignment] = mapped_column(
        enum_type(TemporalAssignment, "temporal_assignment"), default=TemporalAssignment.UNKNOWN
    )
    date_role: Mapped[DateRole | None] = mapped_column(enum_type(DateRole, "date_role"))
    data_status: Mapped[DataStatus] = mapped_column(
        enum_type(DataStatus, "data_status"), default=DataStatus.REPORTED
    )
    missing_reason: Mapped[MissingReason | None] = mapped_column(enum_type(MissingReason, "missing_reason"))
    supersedes_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("claims.id", ondelete="RESTRICT")
    )
    pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="RESTRICT")
    )
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source_release: Mapped[SourceRelease] = relationship(back_populates="claims")
    evidence: Mapped[list[ResolvedClaimEvidence]] = relationship(back_populates="claim")


class ResolvedClaim(Base):
    __tablename__ = "resolved_claims"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    canonical_key: Mapped[str] = mapped_column(String(240))
    version: Mapped[int] = mapped_column(Integer)
    resolved_value: Mapped[dict[str, Any]] = mapped_column(JSONB)
    resolution_method: Mapped[ResolutionMethod] = mapped_column(
        enum_type(ResolutionMethod, "resolution_method")
    )
    comparability_status: Mapped[ComparabilityStatus] = mapped_column(
        enum_type(ComparabilityStatus, "comparability_status"), default=ComparabilityStatus.UNKNOWN
    )
    rationale: Mapped[str] = mapped_column(Text)
    methodology_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("methodologies.id", ondelete="RESTRICT")
    )
    supersedes_resolved_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resolved_claims.id", ondelete="RESTRICT")
    )
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    evidence: Mapped[list[ResolvedClaimEvidence]] = relationship(back_populates="resolved_claim")


class ResolvedClaimEvidence(Base):
    __tablename__ = "resolved_claim_evidence"

    resolved_claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resolved_claims.id", ondelete="RESTRICT"), primary_key=True
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("claims.id", ondelete="RESTRICT"), primary_key=True
    )
    stance: Mapped[str] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_claim: Mapped[ResolvedClaim] = relationship(back_populates="evidence")
    claim: Mapped[Claim] = relationship(back_populates="evidence")


class Geography(Base):
    __tablename__ = "geographies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stable_key: Mapped[str] = mapped_column(String(160), unique=True)
    geography_kind: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GeographyVersion(Base):
    __tablename__ = "geography_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    geography_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("geographies.id", ondelete="RESTRICT"))
    provenance_resolved_claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resolved_claims.id", ondelete="RESTRICT")
    )
    name: Mapped[str] = mapped_column(Text)
    identifier_code: Mapped[str | None] = mapped_column(String(160))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    boundary_geometry: Mapped[Any | None] = mapped_column(Geometry("MULTIPOLYGON", srid=4326))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Event(Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    resolved_claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resolved_claims.id", ondelete="RESTRICT"), unique=True
    )
    event_type: Mapped[str] = mapped_column(String(120))
    canonical_title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    data_status: Mapped[DataStatus] = mapped_column(
        enum_type(DataStatus, "data_status"), default=DataStatus.REPORTED
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EventTime(Base):
    __tablename__ = "event_times"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id", ondelete="RESTRICT"))
    provenance_resolved_claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resolved_claims.id", ondelete="RESTRICT")
    )
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    temporal_precision: Mapped[TemporalPrecision] = mapped_column(enum_type(TemporalPrecision, "temporal_precision"))
    temporal_assignment: Mapped[TemporalAssignment] = mapped_column(enum_type(TemporalAssignment, "temporal_assignment"))
    date_role: Mapped[DateRole] = mapped_column(enum_type(DateRole, "date_role"))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    display_label: Mapped[str | None] = mapped_column(Text)
    exact_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    local_date: Mapped[date | None] = mapped_column(Date)
    timezone_name: Mapped[str | None] = mapped_column(Text)
    utc_offset_minutes: Mapped[int | None] = mapped_column(Integer)
    interpretation: Mapped[str | None] = mapped_column(Text)


class EventLocation(Base):
    __tablename__ = "event_locations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id", ondelete="RESTRICT"))
    geography_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("geography_versions.id", ondelete="RESTRICT")
    )
    provenance_resolved_claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resolved_claims.id", ondelete="RESTRICT")
    )
    point_geometry: Mapped[Any | None] = mapped_column(Geometry("POINT", srid=4326))
    location_role: Mapped[str] = mapped_column(String(80), default="primary")
    display_label: Mapped[str | None] = mapped_column(Text)


class Metric(Base):
    __tablename__ = "metrics"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    metric_key: Mapped[str] = mapped_column(String(160), unique=True)
    display_name: Mapped[str] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(Text)
    definition: Mapped[str] = mapped_column(Text)
    data_status: Mapped[DataStatus] = mapped_column(
        enum_type(DataStatus, "data_status"), default=DataStatus.REPORTED
    )
    provenance_resolved_claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resolved_claims.id", ondelete="RESTRICT")
    )
    methodology_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("methodologies.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Observation(Base):
    __tablename__ = "observations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    metric_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("metrics.id", ondelete="RESTRICT"))
    geography_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("geography_versions.id", ondelete="RESTRICT")
    )
    source_release_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_releases.id", ondelete="RESTRICT")
    )
    provenance_resolved_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resolved_claims.id", ondelete="RESTRICT")
    )
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    temporal_precision: Mapped[TemporalPrecision] = mapped_column(enum_type(TemporalPrecision, "temporal_precision"))
    temporal_assignment: Mapped[TemporalAssignment] = mapped_column(enum_type(TemporalAssignment, "temporal_assignment"))
    date_role: Mapped[DateRole] = mapped_column(
        enum_type(DateRole, "date_role"), default=DateRole.REPORTED
    )
    value_numeric: Mapped[Decimal | None] = mapped_column(Numeric)
    value_text: Mapped[str | None] = mapped_column(Text)
    data_status: Mapped[DataStatus] = mapped_column(enum_type(DataStatus, "data_status"))
    missing_reason: Mapped[MissingReason | None] = mapped_column(enum_type(MissingReason, "missing_reason"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PublicationManifest(Base):
    __tablename__ = "publication_manifests"
    __table_args__ = (
        UniqueConstraint(
            "profile_date",
            "profile_type",
            "version",
            name="publication_manifests_profile_date_profile_type_version_key",
        ),
        UniqueConstraint("supersedes_manifest_id", name="publication_manifests_single_successor"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_date: Mapped[date] = mapped_column(Date)
    profile_type: Mapped[ProfileType] = mapped_column(enum_type(ProfileType, "profile_type"))
    version: Mapped[int] = mapped_column(Integer)
    editorial_revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[PublicationStatus] = mapped_column(
        enum_type(PublicationStatus, "publication_status"), default=PublicationStatus.DRAFT
    )
    content_hash: Mapped[str] = mapped_column(String(64))
    source_snapshot_hash: Mapped[str] = mapped_column(String(64))
    storage_uri: Mapped[str] = mapped_column(Text)
    methodology_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("methodologies.id", ondelete="RESTRICT")
    )
    code_version: Mapped[str] = mapped_column(String(160))
    supersedes_manifest_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("publication_manifests.id", ondelete="RESTRICT")
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DayProfile(Base):
    __tablename__ = "day_profiles"
    __table_args__ = (
        UniqueConstraint("supersedes_day_profile_id", name="day_profiles_single_successor"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_date: Mapped[date] = mapped_column(Date)
    profile_type: Mapped[ProfileType] = mapped_column(enum_type(ProfileType, "profile_type"))
    publication_manifest_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("publication_manifests.id", ondelete="RESTRICT"), unique=True
    )
    content_hash: Mapped[str] = mapped_column(String(64))
    supersedes_day_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("day_profiles.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Correction(Base):
    __tablename__ = "corrections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    correction_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("claims.id", ondelete="RESTRICT")
    )
    original_manifest_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("publication_manifests.id", ondelete="RESTRICT")
    )
    replacement_manifest_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("publication_manifests.id", ondelete="RESTRICT")
    )
    rationale: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceLineage(Base):
    __tablename__ = "source_lineage"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    child_release_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_releases.id", ondelete="RESTRICT")
    )
    parent_release_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_releases.id", ondelete="RESTRICT")
    )
    relationship: Mapped[SourceLineageRelationship] = mapped_column(
        enum_type(SourceLineageRelationship, "source_lineage_relationship")
    )
    methodology_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("methodologies.id", ondelete="RESTRICT")
    )
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Person(Base):
    __tablename__ = "people"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    resolved_claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resolved_claims.id", ondelete="RESTRICT"), unique=True
    )
    canonical_name: Mapped[str] = mapped_column(Text)
    biography_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    resolved_claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resolved_claims.id", ondelete="RESTRICT"), unique=True
    )
    canonical_name: Mapped[str] = mapped_column(Text)
    organization_kind: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EntityAlias(Base):
    __tablename__ = "entity_aliases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("people.id", ondelete="RESTRICT"))
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    geography_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("geographies.id", ondelete="RESTRICT")
    )
    event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("events.id", ondelete="RESTRICT"))
    alias: Mapped[str] = mapped_column(Text)
    language_code: Mapped[str | None] = mapped_column(String(16))
    provenance_resolved_claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resolved_claims.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExternalIdentifier(Base):
    __tablename__ = "external_identifiers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace: Mapped[str] = mapped_column(String(120))
    external_id: Mapped[str] = mapped_column(Text)
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("people.id", ondelete="RESTRICT"))
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    geography_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("geographies.id", ondelete="RESTRICT")
    )
    event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("events.id", ondelete="RESTRICT"))
    provenance_resolved_claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resolved_claims.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EventImpact(Base):
    __tablename__ = "event_impacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id", ondelete="RESTRICT"))
    metric_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("metrics.id", ondelete="RESTRICT"))
    provenance_resolved_claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resolved_claims.id", ondelete="RESTRICT")
    )
    methodology_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("methodologies.id", ondelete="RESTRICT")
    )
    impact_directness: Mapped[ImpactDirectness] = mapped_column(
        enum_type(ImpactDirectness, "impact_directness")
    )
    narrative: Mapped[str] = mapped_column(Text)
    value_numeric: Mapped[Decimal | None] = mapped_column(Numeric)
    data_status: Mapped[DataStatus] = mapped_column(
        enum_type(DataStatus, "data_status"), default=DataStatus.REPORTED
    )
    missing_reason: Mapped[MissingReason | None] = mapped_column(enum_type(MissingReason, "missing_reason"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MetricCoverage(Base):
    __tablename__ = "metric_coverage"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    metric_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("metrics.id", ondelete="RESTRICT"))
    geography_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("geography_versions.id", ondelete="RESTRICT")
    )
    source_release_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_releases.id", ondelete="RESTRICT")
    )
    provenance_resolved_claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resolved_claims.id", ondelete="RESTRICT")
    )
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    coverage_fraction: Mapped[Decimal | None] = mapped_column(Numeric)
    data_status: Mapped[DataStatus] = mapped_column(enum_type(DataStatus, "data_status"))
    missing_reason: Mapped[MissingReason | None] = mapped_column(enum_type(MissingReason, "missing_reason"))
    comparability_status: Mapped[ComparabilityStatus] = mapped_column(
        enum_type(ComparabilityStatus, "comparability_status"), default=ComparabilityStatus.UNKNOWN
    )


class DerivedValue(Base):
    __tablename__ = "derived_values"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    metric_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("metrics.id", ondelete="RESTRICT"))
    geography_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("geography_versions.id", ondelete="RESTRICT")
    )
    methodology_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("methodologies.id", ondelete="RESTRICT"))
    provenance_resolved_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resolved_claims.id", ondelete="RESTRICT")
    )
    value_kind: Mapped[str] = mapped_column(String(120))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    temporal_assignment: Mapped[TemporalAssignment] = mapped_column(
        enum_type(TemporalAssignment, "temporal_assignment")
    )
    value_numeric: Mapped[Decimal | None] = mapped_column(Numeric)
    value_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    data_status: Mapped[DataStatus] = mapped_column(enum_type(DataStatus, "data_status"))
    missing_reason: Mapped[MissingReason | None] = mapped_column(enum_type(MissingReason, "missing_reason"))
    comparability_status: Mapped[ComparabilityStatus] = mapped_column(
        enum_type(ComparabilityStatus, "comparability_status")
    )
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    calculation_version: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DerivedValueInput(Base):
    __tablename__ = "derived_value_inputs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    derived_value_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("derived_values.id", ondelete="RESTRICT")
    )
    observation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("observations.id", ondelete="RESTRICT")
    )
    resolved_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resolved_claims.id", ondelete="RESTRICT")
    )
    input_role: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QualityAssessment(Base):
    __tablename__ = "quality_assessments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_release_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_releases.id", ondelete="RESTRICT")
    )
    claim_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("claims.id", ondelete="RESTRICT"))
    observation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("observations.id", ondelete="RESTRICT")
    )
    derived_value_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("derived_values.id", ondelete="RESTRICT")
    )
    methodology_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("methodologies.id", ondelete="RESTRICT")
    )
    target_methodology_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("methodologies.id", ondelete="RESTRICT")
    )
    legal_review_status: Mapped[LegalReviewStatus] = mapped_column(
        enum_type(LegalReviewStatus, "legal_review_status"), default=LegalReviewStatus.NOT_REQUIRED
    )
    assessment_kind: Mapped[str] = mapped_column(String(120))
    score: Mapped[Decimal | None] = mapped_column(Numeric)
    findings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    public_grade: Mapped[str | None] = mapped_column(String(8))
    public_explanation: Mapped[str | None] = mapped_column(Text)
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QualityCheck(Base):
    __tablename__ = "quality_checks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="RESTRICT")
    )
    check_name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(32))
    subject_type: Mapped[str] = mapped_column(String(80))
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReviewTask(Base):
    __tablename__ = "review_tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    claim_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("claims.id", ondelete="RESTRICT"))
    resolved_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resolved_claims.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(32))
    priority: Mapped[str] = mapped_column(String(32))
    rationale: Mapped[str] = mapped_column(Text)
    assigned_to: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PublicationStatementEvidence(Base):
    __tablename__ = "publication_statement_evidence"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    publication_manifest_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("publication_manifests.id", ondelete="RESTRICT")
    )
    statement_path: Mapped[str] = mapped_column(Text)
    resolved_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resolved_claims.id", ondelete="RESTRICT")
    )
    derived_value_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("derived_values.id", ondelete="RESTRICT")
    )
    evidence_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    evidence_snapshot_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
