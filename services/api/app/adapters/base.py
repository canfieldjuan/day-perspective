from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar
from uuid import UUID

from app.models import DateRole, TemporalAssignment, TemporalPrecision

SourceRecord = TypeVar("SourceRecord")


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
    record_hash: str
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
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=".raw-", dir=destination.parent
        )
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


class SourceAdapter(Protocol, Generic[SourceRecord]):
    metadata: SourceMetadata

    def retrieve(self, *, fixture_path: Path | None = None) -> bytes: ...

    def validate(self, payload: bytes) -> SourceRecord: ...

    def source_record_identity(self, record: SourceRecord) -> str: ...

    def record_to_claims(self, record: SourceRecord) -> tuple[ClaimDraft, ...]: ...
