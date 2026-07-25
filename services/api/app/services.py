from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import event, func, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    Claim,
    ClaimAssertionStatus,
    ComparabilityStatus,
    Correction,
    DayProfile,
    DerivedValue,
    DerivedValueInput,
    Geography,
    GeographyVersion,
    LegalReviewStatus,
    Methodology,
    Metric,
    Observation,
    PipelineRun,
    ProfileType,
    PublicationManifest,
    PublicationStatementEvidence,
    PublicationStatus,
    QualityAssessment,
    ResolutionMethod,
    ResolvedClaim,
    ResolvedClaimEvidence,
    Source,
    SourceLineage,
    SourceRelease,
    TemporalAssignment,
    profile_type_for_date,
)


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def content_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


class PublishedProfileStore(Protocol):
    def write(self, profile_date: date, profile_type: ProfileType, payload: dict[str, Any]) -> str: ...

    def stage_versioned(
        self, profile_date: date, version: int, payload: dict[str, Any]
    ) -> StagedProfileWrite: ...

    def read(self, storage_uri: str, expected_hash: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class PublicationStatementEvidenceInput:
    statement_path: str
    resolved_claim_id: UUID | None = None
    derived_value_id: UUID | None = None


@dataclass(frozen=True)
class SnapshottedStatementEvidence:
    evidence: PublicationStatementEvidenceInput
    snapshot: dict[str, Any]
    snapshot_hash: str


@dataclass
class StagedProfileWrite:
    storage_uri: str
    temporary_path: Path | None
    destination: Path
    expected_hash: str
    store: LocalFilesystemPublishedProfileStore
    created_destination: bool = False

    def finalize(self) -> None:
        if self.temporary_path is None:
            return
        try:
            try:
                os.link(self.temporary_path, self.destination)
                self.created_destination = True
            except FileExistsError:
                self.store.read(self.storage_uri, self.expected_hash)
        finally:
            self.temporary_path.unlink(missing_ok=True)
            self.temporary_path = None

    def discard(self) -> None:
        if self.temporary_path is not None:
            self.temporary_path.unlink(missing_ok=True)
            self.temporary_path = None
        if self.created_destination:
            self.destination.unlink(missing_ok=True)
            self.created_destination = False


_PENDING_PROFILE_WRITES = "pending_profile_writes"


@event.listens_for(Session, "after_commit")
def _finalize_profile_writes(session: Session) -> None:
    if session.in_transaction():
        return
    pending = session.info.pop(_PENDING_PROFILE_WRITES, [])
    for staged in pending:
        staged.finalize()


@event.listens_for(Session, "after_rollback")
def _discard_profile_writes(session: Session) -> None:
    pending = session.info.pop(_PENDING_PROFILE_WRITES, [])
    for staged in pending:
        staged.discard()


class LocalFilesystemPublishedProfileStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def write(self, profile_date: date, profile_type: ProfileType, payload: dict[str, Any]) -> str:
        digest = content_hash(payload)
        relative = Path(profile_type.value) / f"{profile_date.isoformat()}-{digest[:16]}.json"
        destination = (self.root / relative).resolve()
        if not destination.is_relative_to(self.root):
            raise RuntimeError("Refused profile write outside the configured storage root.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            self.read(relative.as_posix(), digest)
            return relative.as_posix()
        descriptor, temporary_path = tempfile.mkstemp(prefix=".profile-", dir=destination.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(canonical_json_bytes(payload))
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary_path, destination)
            except FileExistsError:
                self.read(relative.as_posix(), digest)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)
        return relative.as_posix()

    def write_versioned(
        self,
        profile_date: date,
        version: int,
        payload: dict[str, Any],
    ) -> str:
        staged = self.stage_versioned(profile_date, version, payload)
        staged.finalize()
        return staged.storage_uri

    def stage_versioned(
        self,
        profile_date: date,
        version: int,
        payload: dict[str, Any],
    ) -> StagedProfileWrite:
        digest = content_hash(payload)
        relative = Path("day") / profile_date.isoformat() / f"profile-v{version}.json"
        destination = (self.root / relative).resolve()
        if not destination.is_relative_to(self.root):
            raise RuntimeError("Refused profile write outside the configured storage root.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            try:
                self.read(relative.as_posix(), digest)
                return StagedProfileWrite(
                    relative.as_posix(), None, destination, digest, self
                )
            except RuntimeError:
                destination.unlink()
        descriptor, temporary_path = tempfile.mkstemp(prefix=".profile-", dir=destination.parent)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json_bytes(payload))
            handle.flush()
            os.fsync(handle.fileno())
        return StagedProfileWrite(
            relative.as_posix(), Path(temporary_path), destination, digest, self
        )

    def read(self, storage_uri: str, expected_hash: str) -> dict[str, Any]:
        candidate = (self.root / storage_uri).resolve()
        if not candidate.is_relative_to(self.root):
            raise RuntimeError("Refused profile read outside the configured storage root.")
        payload = json.loads(candidate.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or content_hash(payload) != expected_hash:
            raise RuntimeError("Published profile content did not match its manifest hash.")
        return payload


def create_source_release(
    session: Session,
    *,
    source_id: UUID,
    release_label: str,
    source_url: str,
    raw_storage_uri: str,
    raw_record_count: int,
    raw_bytes: bytes | None = None,
    raw_checksum_sha256: str | None = None,
    pipeline_run_id: UUID | None = None,
    metadata_json: dict[str, Any] | None = None,
    legal_review_status: LegalReviewStatus = LegalReviewStatus.PENDING,
) -> SourceRelease:
    calculated_checksum = hashlib.sha256(raw_bytes).hexdigest() if raw_bytes is not None else None
    if raw_checksum_sha256 is not None and calculated_checksum is not None:
        if raw_checksum_sha256 != calculated_checksum:
            raise ValueError("Provided raw checksum does not match the supplied source bytes.")
    checksum = raw_checksum_sha256 or calculated_checksum
    if checksum is None:
        raise ValueError("A source release must retain a raw SHA-256 checksum.")
    release = SourceRelease(
        source_id=source_id,
        release_label=release_label,
        source_url=source_url,
        raw_storage_uri=raw_storage_uri,
        raw_checksum_sha256=checksum,
        raw_record_count=raw_record_count,
        pipeline_run_id=pipeline_run_id,
        metadata_json=metadata_json or {},
        legal_review_status=legal_review_status,
    )
    session.add(release)
    session.flush()
    return release


def create_claim(
    session: Session,
    *,
    source_release_id: UUID,
    source_record_locator: str,
    claim_type: str,
    assertion_text: str | None,
    assertion_json: dict[str, Any] | None = None,
    assertion_status: ClaimAssertionStatus = ClaimAssertionStatus.IMPORTED,
    source_record_hash_sha256: str | None = None,
    unit: str | None = None,
    lower_bound: Decimal | None = None,
    upper_bound: Decimal | None = None,
) -> Claim:
    if source_record_hash_sha256 is None:
        release = session.get(SourceRelease, source_release_id)
        if release is None:
            raise ValueError("A claim requires an existing source release.")
        if release.raw_record_count != 1:
            raise ValueError(
                "Claims from multi-record releases require a source-record hash."
            )
        source_record_hash_sha256 = release.raw_checksum_sha256
    claim = Claim(
        source_release_id=source_release_id,
        source_record_locator=source_record_locator,
        claim_type=claim_type,
        assertion_text=assertion_text,
        assertion_json=assertion_json,
        assertion_status=assertion_status,
        source_record_hash_sha256=source_record_hash_sha256,
        unit=unit,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
    )
    session.add(claim)
    session.flush()
    return claim


def supersede_claim(
    session: Session,
    *,
    prior_claim: Claim,
    assertion_text: str | None,
    assertion_json: dict[str, Any] | None = None,
    unit: str | None = None,
    lower_bound: Decimal | None = None,
    upper_bound: Decimal | None = None,
) -> Claim:
    prior_claim.assertion_status = ClaimAssertionStatus.SUPERSEDED
    replacement = Claim(
        source_release_id=prior_claim.source_release_id,
        source_record_locator=prior_claim.source_record_locator,
        claim_type=prior_claim.claim_type,
        assertion_text=assertion_text,
        assertion_json=assertion_json,
        assertion_status=ClaimAssertionStatus.CANDIDATE,
        supersedes_claim_id=prior_claim.id,
        source_record_hash_sha256=prior_claim.source_record_hash_sha256,
        pipeline_run_id=prior_claim.pipeline_run_id,
        temporal_precision=prior_claim.temporal_precision,
        temporal_assignment=prior_claim.temporal_assignment,
        temporal_start=prior_claim.temporal_start,
        temporal_end=prior_claim.temporal_end,
        date_role=prior_claim.date_role,
        unit=unit,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
    )
    session.add(replacement)
    session.flush()
    return replacement


def resolve_claim(
    session: Session,
    *,
    canonical_key: str,
    resolved_value: dict[str, Any],
    rationale: str,
    supporting_claim_ids: Iterable[UUID],
    dissenting_claim_ids: Iterable[UUID] = (),
    resolution_method: ResolutionMethod = ResolutionMethod.EDITORIAL_REVIEW,
    methodology_id: UUID | None = None,
    supersedes_resolved_claim_id: UUID | None = None,
) -> ResolvedClaim:
    supporting = list(supporting_claim_ids)
    dissenting = list(dissenting_claim_ids)
    if not supporting:
        raise ValueError("A resolved claim requires at least one supporting claim.")
    if set(supporting) & set(dissenting):
        raise ValueError("Evidence cannot be both supporting and dissenting.")
    latest = session.scalar(
        select(ResolvedClaim)
        .where(ResolvedClaim.canonical_key == canonical_key)
        .order_by(ResolvedClaim.version.desc())
    )
    if latest is None:
        if supersedes_resolved_claim_id is not None:
            raise ValueError(
                "A first resolved-claim version cannot supersede a predecessor."
            )
        next_version = 1
    else:
        if supersedes_resolved_claim_id != latest.id:
            raise ValueError(
                "A resolved claim must supersede the latest version of the same "
                "canonical key."
            )
        next_version = latest.version + 1
    resolved = ResolvedClaim(
        canonical_key=canonical_key,
        version=next_version,
        resolved_value=resolved_value,
        resolution_method=resolution_method,
        comparability_status=ComparabilityStatus.UNKNOWN,
        rationale=rationale,
        methodology_id=methodology_id,
        supersedes_resolved_claim_id=supersedes_resolved_claim_id,
    )
    session.add(resolved)
    session.flush()
    session.add_all(
        [
            ResolvedClaimEvidence(resolved_claim_id=resolved.id, claim_id=claim_id, stance="supporting")
            for claim_id in supporting
        ]
        + [
            ResolvedClaimEvidence(resolved_claim_id=resolved.id, claim_id=claim_id, stance="dissenting")
            for claim_id in dissenting
        ]
    )
    session.flush()
    return resolved


def classify_period_allocation(*, allocated_uniformly: bool) -> TemporalAssignment:
    return (
        TemporalAssignment.UNIFORM_PERIOD_ALLOCATION
        if allocated_uniformly
        else TemporalAssignment.MODELED_PERIOD_ALLOCATION
    )


def _profile_statement_paths(
    payload: dict[str, Any], *, profile_date: date, profile_type: ProfileType
) -> set[str]:
    if (
        payload.get("schema_version") != "1"
        or payload.get("date") != profile_date.isoformat()
        or payload.get("profile_type") != profile_type.value
    ):
        raise ValueError("Published profile envelope does not match the target date and profile type.")
    sections = payload.get("sections")
    if not isinstance(sections, dict):
        raise ValueError("A published profile must contain a sections object.")
    statement_paths: set[str] = set()
    for section_key, statements in sections.items():
        if not isinstance(section_key, str) or not isinstance(statements, list):
            raise ValueError("Published profile sections must be keyed statement lists.")
        for index, statement in enumerate(statements):
            if (
                not isinstance(statement, dict)
                or not isinstance(statement.get("statement_id"), str)
                or not isinstance(statement.get("statement"), str)
            ):
                raise ValueError("Every published statement must be a structured statement object.")
            statement_paths.add(f"/sections/{section_key}/{index}")
    return statement_paths


def _validate_statement_evidence(
    session: Session,
    payload: dict[str, Any],
    statement_evidence: Iterable[PublicationStatementEvidenceInput],
    *,
    profile_date: date,
    profile_type: ProfileType,
) -> list[PublicationStatementEvidenceInput]:
    statement_paths = _profile_statement_paths(
        payload,
        profile_date=profile_date,
        profile_type=profile_type,
    )
    evidence = list(statement_evidence)
    evidence_paths: set[str] = set()
    for item in evidence:
        if bool(item.resolved_claim_id) == bool(item.derived_value_id):
            raise ValueError("Each published statement must reference exactly one resolved claim or derived value.")
        if item.statement_path in evidence_paths:
            raise ValueError("Published statement evidence paths must be unique.")
        evidence_paths.add(item.statement_path)
    if evidence_paths != statement_paths:
        raise ValueError("Every published statement requires exactly one provenance mapping.")
    derived_value_ids = {item.derived_value_id for item in evidence if item.derived_value_id is not None}
    if derived_value_ids:
        derived_values = list(
            session.scalars(select(DerivedValue).where(DerivedValue.id.in_(derived_value_ids)))
        )
        derived_values_by_id = {value.id: value for value in derived_values}
        if derived_values_by_id.keys() != derived_value_ids:
            raise ValueError("Published statement evidence references an unknown derived value.")
        input_derived_value_ids = set(
            session.scalars(
                select(DerivedValueInput.derived_value_id).where(
                    DerivedValueInput.derived_value_id.in_(derived_value_ids)
                )
            )
        )
        if any(
            value.provenance_resolved_claim_id is None and value.id not in input_derived_value_ids
            for value in derived_values
        ):
            raise ValueError("Published derived statements require traceable resolved-claim or input lineage.")
    return evidence


def _optional_date(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _optional_enum(value: Any) -> str | None:
    return str(value.value) if value is not None else None


def _methodology_core_snapshot(methodology: Methodology | None) -> dict[str, Any] | None:
    if methodology is None:
        return None
    return {
        "id": str(methodology.id),
        "slug": methodology.slug,
        "version": methodology.version,
        "name": methodology.name,
        "description": methodology.description,
        "method_kind": methodology.method_kind,
        "formula": methodology.formula,
        "code_version": methodology.code_version,
        "definition_hash": methodology.definition_hash,
        "legal_review_status": methodology.legal_review_status.value,
        "created_at": methodology.created_at.isoformat(),
    }


def _quality_assessment_snapshots(
    session: Session,
    *,
    source_release_id: UUID | None = None,
    claim_id: UUID | None = None,
    observation_id: UUID | None = None,
    derived_value_id: UUID | None = None,
    target_methodology_id: UUID | None = None,
) -> list[dict[str, Any]]:
    conditions = []
    for column, value in (
        (QualityAssessment.source_release_id, source_release_id),
        (QualityAssessment.claim_id, claim_id),
        (QualityAssessment.observation_id, observation_id),
        (QualityAssessment.derived_value_id, derived_value_id),
        (QualityAssessment.target_methodology_id, target_methodology_id),
    ):
        if value is not None:
            conditions.append(column == value)
    if not conditions:
        return []
    rows = list(
        session.scalars(select(QualityAssessment).where(or_(*conditions)))
    )
    snapshots: list[dict[str, Any]] = []
    for assessment in sorted(rows, key=lambda item: str(item.id)):
        assessment_methodology = (
            session.get(Methodology, assessment.methodology_id)
            if assessment.methodology_id is not None
            else None
        )
        snapshots.append(
            {
                "id": str(assessment.id),
                "source_release_id": (
                    str(assessment.source_release_id)
                    if assessment.source_release_id is not None
                    else None
                ),
                "claim_id": str(assessment.claim_id) if assessment.claim_id is not None else None,
                "observation_id": (
                    str(assessment.observation_id)
                    if assessment.observation_id is not None
                    else None
                ),
                "derived_value_id": (
                    str(assessment.derived_value_id)
                    if assessment.derived_value_id is not None
                    else None
                ),
                "target_methodology_id": (
                    str(assessment.target_methodology_id)
                    if assessment.target_methodology_id is not None
                    else None
                ),
                "assessment_kind": assessment.assessment_kind,
                "score": str(assessment.score) if assessment.score is not None else None,
                "findings": assessment.findings,
                "public_grade": assessment.public_grade,
                "public_explanation": assessment.public_explanation,
                "legal_review_status": assessment.legal_review_status.value,
                "assessed_at": assessment.assessed_at.isoformat(),
                "assessment_methodology": _methodology_core_snapshot(
                    assessment_methodology
                ),
            }
        )
    return snapshots


def _methodology_snapshot(
    session: Session, methodology: Methodology | None
) -> dict[str, Any] | None:
    snapshot = _methodology_core_snapshot(methodology)
    if methodology is None or snapshot is None:
        return None
    snapshot["quality_assessments"] = _quality_assessment_snapshots(
        session, target_methodology_id=methodology.id
    )
    return snapshot


def _pipeline_run_snapshot(session: Session, pipeline_run_id: UUID | None) -> dict[str, Any] | None:
    if pipeline_run_id is None:
        return None
    pipeline_run = session.get(PipelineRun, pipeline_run_id)
    if pipeline_run is None:
        raise ValueError("Publication evidence references an unknown pipeline run.")
    return {
        "id": str(pipeline_run.id),
        "pipeline_name": pipeline_run.pipeline_name,
        "code_version": pipeline_run.code_version,
        "configuration_hash": pipeline_run.configuration_hash,
        "status": pipeline_run.status,
        "started_at": pipeline_run.started_at.isoformat(),
        "completed_at": _optional_date(pipeline_run.completed_at),
        "details": pipeline_run.details,
    }


def _source_release_snapshot(
    session: Session,
    release_id: UUID,
    *,
    lineage_stack: frozenset[UUID] = frozenset(),
) -> dict[str, Any]:
    if release_id in lineage_stack:
        raise ValueError("Publication evidence contains cyclic source lineage.")
    release = session.get(SourceRelease, release_id)
    if release is None:
        raise ValueError("Publication evidence references an unknown source release.")
    source = session.get(Source, release.source_id)
    if source is None:
        raise ValueError("Publication evidence references a source release without a source.")
    lineage_rows = list(
        session.scalars(
            select(SourceLineage).where(SourceLineage.child_release_id == release.id)
        )
    )
    next_lineage_stack = lineage_stack | {release.id}
    lineage: list[dict[str, Any]] = []
    for edge in sorted(
        lineage_rows,
        key=lambda item: (item.relationship.value, str(item.parent_release_id), str(item.id)),
    ):
        methodology = (
            session.get(Methodology, edge.methodology_id)
            if edge.methodology_id is not None
            else None
        )
        lineage.append(
            {
                "id": str(edge.id),
                "relationship": edge.relationship.value,
                "note": edge.note,
                "created_at": edge.created_at.isoformat(),
                "methodology": _methodology_snapshot(session, methodology),
                "parent_release": _source_release_snapshot(
                    session,
                    edge.parent_release_id,
                    lineage_stack=next_lineage_stack,
                ),
            }
        )
    return {
        "source": {
            "id": str(source.id),
            "slug": source.slug,
            "name": source.name,
            "publisher": source.publisher,
            "canonical_url": source.canonical_url,
            "legal_review_status": source.legal_review_status.value,
        },
        "release": {
            "id": str(release.id),
            "release_label": release.release_label,
            "source_url": release.source_url,
            "published_at": _optional_date(release.published_at),
            "retrieved_at": _optional_date(release.retrieved_at),
            "ingested_at": _optional_date(release.ingested_at),
            "raw_storage_uri": release.raw_storage_uri,
            "raw_checksum_sha256": release.raw_checksum_sha256,
            "raw_record_count": release.raw_record_count,
            "legal_review_status": release.legal_review_status.value,
            "pipeline_run": _pipeline_run_snapshot(session, release.pipeline_run_id),
            "metadata": release.metadata_json,
            "quality_assessments": _quality_assessment_snapshots(
                session, source_release_id=release.id
            ),
            "lineage": lineage,
        },
    }


def _claim_snapshot(session: Session, claim: Claim) -> dict[str, Any]:
    return {
        "id": str(claim.id),
        "source_record_locator": claim.source_record_locator,
        "source_record_hash_sha256": claim.source_record_hash_sha256,
        "claim_type": claim.claim_type,
        "assertion_status": claim.assertion_status.value,
        "assertion_text": claim.assertion_text,
        "assertion": claim.assertion_json,
        "unit": claim.unit,
        "lower_bound": str(claim.lower_bound) if claim.lower_bound is not None else None,
        "upper_bound": str(claim.upper_bound) if claim.upper_bound is not None else None,
        "temporal_start": _optional_date(claim.temporal_start),
        "temporal_end": _optional_date(claim.temporal_end),
        "temporal_precision": claim.temporal_precision.value,
        "temporal_assignment": claim.temporal_assignment.value,
        "date_role": _optional_enum(claim.date_role),
        "data_status": claim.data_status.value,
        "missing_reason": _optional_enum(claim.missing_reason),
        "supersedes_claim_id": (
            str(claim.supersedes_claim_id) if claim.supersedes_claim_id is not None else None
        ),
        "pipeline_run": _pipeline_run_snapshot(session, claim.pipeline_run_id),
        "imported_at": claim.imported_at.isoformat(),
        "source_release": _source_release_snapshot(session, claim.source_release_id),
        "quality_assessments": _quality_assessment_snapshots(session, claim_id=claim.id),
    }


def _resolved_claim_snapshot(session: Session, resolved_claim_id: UUID) -> dict[str, Any]:
    resolved = session.get(ResolvedClaim, resolved_claim_id)
    if resolved is None:
        raise ValueError("Publication evidence references an unknown resolved claim.")
    evidence_rows = list(
        session.scalars(
            select(ResolvedClaimEvidence).where(
                ResolvedClaimEvidence.resolved_claim_id == resolved.id
            )
        )
    )
    ordered_evidence = sorted(
        evidence_rows,
        key=lambda item: (0 if item.stance == "supporting" else 1, str(item.claim_id)),
    )
    if not any(item.stance == "supporting" for item in ordered_evidence):
        raise ValueError("Published resolved claims require supporting imported evidence.")
    evidence: list[dict[str, Any]] = []
    for item in ordered_evidence:
        claim = session.get(Claim, item.claim_id)
        if claim is None:
            raise ValueError("Resolved publication evidence references an unknown imported claim.")
        evidence.append(
            {
                "stance": item.stance,
                "note": item.note,
                "claim": _claim_snapshot(session, claim),
            }
        )
    methodology = (
        session.get(Methodology, resolved.methodology_id)
        if resolved.methodology_id is not None
        else None
    )
    return {
        "schema_version": "1",
        "root_type": "resolved_claim",
        "resolved_claim": {
            "id": str(resolved.id),
            "canonical_key": resolved.canonical_key,
            "version": resolved.version,
            "resolved_value": resolved.resolved_value,
            "resolution_method": resolved.resolution_method.value,
            "comparability_status": resolved.comparability_status.value,
            "rationale": resolved.rationale,
            "supersedes_resolved_claim_id": (
                str(resolved.supersedes_resolved_claim_id)
                if resolved.supersedes_resolved_claim_id is not None
                else None
            ),
            "resolved_at": resolved.resolved_at.isoformat(),
            "methodology": _methodology_snapshot(session, methodology),
        },
        "evidence": evidence,
    }


def _geography_version_snapshot(
    session: Session, geography_version_id: UUID | None
) -> dict[str, Any] | None:
    if geography_version_id is None:
        return None
    version = session.get(GeographyVersion, geography_version_id)
    if version is None:
        raise ValueError("Publication evidence references an unknown geography version.")
    geography = session.get(Geography, version.geography_id)
    if geography is None:
        raise ValueError("Publication evidence references a geography version without a geography.")
    boundary_geojson = session.scalar(
        select(func.ST_AsGeoJSON(GeographyVersion.boundary_geometry)).where(
            GeographyVersion.id == version.id
        )
    )
    return {
        "id": str(version.id),
        "geography": {
            "id": str(geography.id),
            "stable_key": geography.stable_key,
            "geography_kind": geography.geography_kind,
            "created_at": geography.created_at.isoformat(),
        },
        "name": version.name,
        "identifier_code": version.identifier_code,
        "valid_from": version.valid_from.isoformat(),
        "valid_to": _optional_date(version.valid_to),
        "boundary_geojson": (
            json.loads(boundary_geojson) if boundary_geojson is not None else None
        ),
        "created_at": version.created_at.isoformat(),
        "provenance_resolved_claim": _resolved_claim_snapshot(
            session, version.provenance_resolved_claim_id
        ),
    }


def _metric_snapshot(session: Session, metric_id: UUID | None) -> dict[str, Any] | None:
    if metric_id is None:
        return None
    metric = session.get(Metric, metric_id)
    if metric is None:
        raise ValueError("Publication evidence references an unknown metric.")
    methodology = (
        session.get(Methodology, metric.methodology_id)
        if metric.methodology_id is not None
        else None
    )
    return {
        "id": str(metric.id),
        "metric_key": metric.metric_key,
        "display_name": metric.display_name,
        "unit": metric.unit,
        "definition": metric.definition,
        "data_status": metric.data_status.value,
        "created_at": metric.created_at.isoformat(),
        "methodology": _methodology_snapshot(session, methodology),
        "provenance_resolved_claim": _resolved_claim_snapshot(
            session, metric.provenance_resolved_claim_id
        ),
    }


def _observation_snapshot(session: Session, observation_id: UUID) -> dict[str, Any]:
    observation = session.get(Observation, observation_id)
    if observation is None:
        raise ValueError("Derived publication evidence references an unknown observation.")
    provenance = (
        _resolved_claim_snapshot(session, observation.provenance_resolved_claim_id)
        if observation.provenance_resolved_claim_id is not None
        else None
    )
    return {
        "id": str(observation.id),
        "metric": _metric_snapshot(session, observation.metric_id),
        "geography_version": _geography_version_snapshot(
            session, observation.geography_version_id
        ),
        "period_start": observation.period_start.isoformat(),
        "period_end": _optional_date(observation.period_end),
        "temporal_precision": observation.temporal_precision.value,
        "temporal_assignment": observation.temporal_assignment.value,
        "date_role": observation.date_role.value,
        "value_numeric": (
            str(observation.value_numeric) if observation.value_numeric is not None else None
        ),
        "value_text": observation.value_text,
        "data_status": observation.data_status.value,
        "missing_reason": _optional_enum(observation.missing_reason),
        "source_release": _source_release_snapshot(session, observation.source_release_id),
        "provenance_resolved_claim": provenance,
        "quality_assessments": _quality_assessment_snapshots(
            session, observation_id=observation.id
        ),
    }


def _derived_value_snapshot(session: Session, derived_value_id: UUID) -> dict[str, Any]:
    derived = session.get(DerivedValue, derived_value_id)
    if derived is None:
        raise ValueError("Publication evidence references an unknown derived value.")
    methodology = session.get(Methodology, derived.methodology_id)
    if methodology is None:
        raise ValueError("Derived publication evidence requires a versioned methodology.")
    input_rows = list(
        session.scalars(
            select(DerivedValueInput).where(DerivedValueInput.derived_value_id == derived.id)
        )
    )
    ordered_inputs = sorted(input_rows, key=lambda item: (item.input_role, str(item.id)))
    inputs: list[dict[str, Any]] = []
    for item in ordered_inputs:
        if item.resolved_claim_id is not None:
            root = _resolved_claim_snapshot(session, item.resolved_claim_id)
        elif item.observation_id is not None:
            root = {
                "schema_version": "1",
                "root_type": "observation",
                "observation": _observation_snapshot(session, item.observation_id),
            }
        else:
            raise ValueError("Derived publication evidence contains an empty input.")
        inputs.append({"input_role": item.input_role, "root": root})
    direct_provenance = (
        _resolved_claim_snapshot(session, derived.provenance_resolved_claim_id)
        if derived.provenance_resolved_claim_id is not None
        else None
    )
    if direct_provenance is None and not inputs:
        raise ValueError("Published derived values require complete durable lineage.")
    return {
        "schema_version": "1",
        "root_type": "derived_value",
        "derived_value": {
            "id": str(derived.id),
            "metric": _metric_snapshot(session, derived.metric_id),
            "geography_version": _geography_version_snapshot(
                session, derived.geography_version_id
            ),
            "value_kind": derived.value_kind,
            "period_start": derived.period_start.isoformat(),
            "period_end": _optional_date(derived.period_end),
            "temporal_assignment": derived.temporal_assignment.value,
            "value_numeric": (
                str(derived.value_numeric) if derived.value_numeric is not None else None
            ),
            "value": derived.value_json,
            "data_status": derived.data_status.value,
            "missing_reason": _optional_enum(derived.missing_reason),
            "comparability_status": derived.comparability_status.value,
            "input_fingerprint": derived.input_fingerprint,
            "calculation_version": derived.calculation_version,
            "created_at": derived.created_at.isoformat(),
            "methodology": _methodology_snapshot(session, methodology),
            "quality_assessments": _quality_assessment_snapshots(
                session, derived_value_id=derived.id
            ),
        },
        "direct_provenance": direct_provenance,
        "inputs": inputs,
    }


def _snapshot_statement_evidence(
    session: Session,
    evidence: Iterable[PublicationStatementEvidenceInput],
) -> list[SnapshottedStatementEvidence]:
    snapshotted: list[SnapshottedStatementEvidence] = []
    for item in evidence:
        if item.resolved_claim_id is not None:
            snapshot = _resolved_claim_snapshot(session, item.resolved_claim_id)
        elif item.derived_value_id is not None:
            snapshot = _derived_value_snapshot(session, item.derived_value_id)
        else:
            raise ValueError("Publication evidence has no provenance root.")
        snapshotted.append(
            SnapshottedStatementEvidence(
                evidence=item,
                snapshot=snapshot,
                snapshot_hash=content_hash(snapshot),
            )
        )
    return sorted(snapshotted, key=lambda item: item.evidence.statement_path)


def _source_snapshot_hash(evidence: Iterable[SnapshottedStatementEvidence]) -> str:
    return content_hash(
        {
            "schema_version": "1",
            "statements": [
                {
                    "statement_path": item.evidence.statement_path,
                    "evidence_snapshot_hash": item.snapshot_hash,
                }
                for item in evidence
            ],
        }
    )


def _validate_profile_supersession(
    session: Session,
    *,
    profile_date: date,
    profile_type: ProfileType,
    supersedes_manifest_id: UUID | None,
    supersedes_day_profile_id: UUID | None,
) -> None:
    if bool(supersedes_manifest_id) != bool(supersedes_day_profile_id):
        raise ValueError("Profile supersession requires both manifest and day-profile predecessors.")
    if supersedes_manifest_id is None or supersedes_day_profile_id is None:
        return
    previous_manifest = session.get(PublicationManifest, supersedes_manifest_id)
    previous_profile = session.get(DayProfile, supersedes_day_profile_id)
    if previous_manifest is None or previous_manifest.status != PublicationStatus.PUBLISHED:
        raise ValueError("Profile supersession requires a published predecessor manifest.")
    if previous_profile is None or previous_profile.publication_manifest_id != previous_manifest.id:
        raise ValueError("Profile supersession must reference the predecessor day profile.")
    if previous_manifest.profile_date != profile_date or previous_manifest.profile_type != profile_type:
        raise ValueError("Profile supersession must retain the same date and profile type.")


def publish_day_profile(
    session: Session,
    *,
    store: PublishedProfileStore,
    profile_date: date,
    profile_type: ProfileType,
    payload: dict[str, Any],
    statement_evidence: Iterable[PublicationStatementEvidenceInput],
    supersedes_manifest_id: UUID | None = None,
    supersedes_day_profile_id: UUID | None = None,
    methodology_id: UUID | None = None,
    editorial_revision: int = 1,
    manifest_metadata: dict[str, Any] | None = None,
) -> DayProfile:
    if profile_type_for_date(profile_date) != profile_type:
        raise ValueError("The profile type does not match the public date band.")
    reserved_metadata_keys = {
        "evidence_snapshot_schema_version",
        "statement_evidence_hashes",
    }
    conflicting_metadata_keys = reserved_metadata_keys.intersection(
        manifest_metadata or {}
    )
    if conflicting_metadata_keys:
        raise ValueError(
            "Manifest metadata contains publisher-reserved keys: "
            + ", ".join(sorted(conflicting_metadata_keys))
        )
    evidence = _validate_statement_evidence(
        session,
        payload,
        statement_evidence,
        profile_date=profile_date,
        profile_type=profile_type,
    )
    snapshotted_evidence = _snapshot_statement_evidence(session, evidence)
    _validate_profile_supersession(
        session,
        profile_date=profile_date,
        profile_type=profile_type,
        supersedes_manifest_id=supersedes_manifest_id,
        supersedes_day_profile_id=supersedes_day_profile_id,
    )
    version = (
        session.scalar(
            select(func.coalesce(func.max(PublicationManifest.version), 0)).where(
                PublicationManifest.profile_date == profile_date,
                PublicationManifest.profile_type == profile_type,
            )
        )
        or 0
    ) + 1
    digest = content_hash(payload)
    manifest = PublicationManifest(
        profile_date=profile_date,
        profile_type=profile_type,
        version=version,
        editorial_revision=editorial_revision,
        status=PublicationStatus.DRAFT,
        content_hash=digest,
        source_snapshot_hash=_source_snapshot_hash(snapshotted_evidence),
        storage_uri="pending://local-filesystem-write",
        code_version=get_settings().service_version,
        methodology_id=methodology_id,
        supersedes_manifest_id=supersedes_manifest_id,
        metadata_json={
            **(manifest_metadata or {}),
            "evidence_snapshot_schema_version": "1",
            "statement_evidence_hashes": [
                {
                    "statement_path": item.evidence.statement_path,
                    "evidence_snapshot_hash": item.snapshot_hash,
                }
                for item in snapshotted_evidence
            ],
        },
    )
    session.add(manifest)
    session.flush()
    session.add_all(
        [
            PublicationStatementEvidence(
                publication_manifest_id=manifest.id,
                statement_path=item.evidence.statement_path,
                resolved_claim_id=item.evidence.resolved_claim_id,
                derived_value_id=item.evidence.derived_value_id,
                evidence_snapshot=item.snapshot,
                evidence_snapshot_hash=item.snapshot_hash,
            )
            for item in snapshotted_evidence
        ]
    )
    session.flush()
    staged = store.stage_versioned(profile_date, version, payload)
    staged.finalize()
    manifest.storage_uri = staged.storage_uri
    session.info.setdefault(_PENDING_PROFILE_WRITES, []).append(staged)
    manifest.status = PublicationStatus.PUBLISHED
    manifest.published_at = datetime.now(UTC)
    session.flush()
    profile = DayProfile(
        profile_date=profile_date,
        profile_type=profile_type,
        publication_manifest_id=manifest.id,
        content_hash=digest,
        supersedes_day_profile_id=supersedes_day_profile_id,
    )
    session.add(profile)
    session.flush()
    return profile


def record_correction(
    session: Session,
    *,
    original_manifest_id: UUID,
    replacement_manifest_id: UUID,
    rationale: str,
) -> Correction:
    original = session.get(PublicationManifest, original_manifest_id)
    replacement = session.get(PublicationManifest, replacement_manifest_id)
    if original is None or original.status != PublicationStatus.PUBLISHED:
        raise ValueError("Corrections require an already published original manifest.")
    if replacement is None or replacement.status != PublicationStatus.PUBLISHED:
        raise ValueError("Corrections require an already published replacement manifest.")
    if replacement.supersedes_manifest_id != original.id:
        raise ValueError("Correction replacements must supersede the original manifest.")
    if replacement.profile_date != original.profile_date or replacement.profile_type != original.profile_type:
        raise ValueError("Correction replacements must retain the original date and profile type.")
    original_profile = session.scalar(
        select(DayProfile).where(DayProfile.publication_manifest_id == original.id)
    )
    replacement_profile = session.scalar(
        select(DayProfile).where(DayProfile.publication_manifest_id == replacement.id)
    )
    if original_profile is None or replacement_profile is None:
        raise ValueError("Corrections require published day profiles for both manifests.")
    if replacement_profile.supersedes_day_profile_id != original_profile.id:
        raise ValueError("Correction replacements must supersede the original day profile.")
    correction = Correction(
        original_manifest_id=original_manifest_id,
        replacement_manifest_id=replacement_manifest_id,
        rationale=rationale,
    )
    session.add(correction)
    session.flush()
    return correction
