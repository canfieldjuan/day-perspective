from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import func, select
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
    ProfileType,
    PublicationManifest,
    PublicationStatementEvidence,
    PublicationStatus,
    ResolutionMethod,
    ResolvedClaim,
    ResolvedClaimEvidence,
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

    def read(self, storage_uri: str, expected_hash: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class PublicationStatementEvidenceInput:
    statement_path: str
    resolved_claim_id: UUID | None = None
    derived_value_id: UUID | None = None


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
) -> Claim:
    claim = Claim(
        source_release_id=source_release_id,
        source_record_locator=source_record_locator,
        claim_type=claim_type,
        assertion_text=assertion_text,
        assertion_json=assertion_json,
        assertion_status=assertion_status,
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
) -> ResolvedClaim:
    supporting = list(supporting_claim_ids)
    dissenting = list(dissenting_claim_ids)
    if not supporting:
        raise ValueError("A resolved claim requires at least one supporting claim.")
    if set(supporting) & set(dissenting):
        raise ValueError("Evidence cannot be both supporting and dissenting.")
    next_version = (
        session.scalar(
            select(func.coalesce(func.max(ResolvedClaim.version), 0)).where(
                ResolvedClaim.canonical_key == canonical_key
            )
        )
        or 0
    ) + 1
    resolved = ResolvedClaim(
        canonical_key=canonical_key,
        version=next_version,
        resolved_value=resolved_value,
        resolution_method=ResolutionMethod.EDITORIAL_REVIEW,
        comparability_status=ComparabilityStatus.UNKNOWN,
        rationale=rationale,
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
    source_snapshot_hash: str,
    statement_evidence: Iterable[PublicationStatementEvidenceInput],
    supersedes_manifest_id: UUID | None = None,
    supersedes_day_profile_id: UUID | None = None,
) -> DayProfile:
    if profile_type_for_date(profile_date) != profile_type:
        raise ValueError("The profile type does not match the public date band.")
    evidence = _validate_statement_evidence(
        session,
        payload,
        statement_evidence,
        profile_date=profile_date,
        profile_type=profile_type,
    )
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
        status=PublicationStatus.DRAFT,
        content_hash=digest,
        source_snapshot_hash=source_snapshot_hash,
        storage_uri="pending://local-filesystem-write",
        code_version=get_settings().service_version,
        supersedes_manifest_id=supersedes_manifest_id,
    )
    session.add(manifest)
    session.flush()
    session.add_all(
        [
            PublicationStatementEvidence(
                publication_manifest_id=manifest.id,
                statement_path=item.statement_path,
                resolved_claim_id=item.resolved_claim_id,
                derived_value_id=item.derived_value_id,
            )
            for item in evidence
        ]
    )
    session.flush()
    manifest.storage_uri = store.write(profile_date, profile_type, payload)
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
