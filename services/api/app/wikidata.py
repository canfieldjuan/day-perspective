from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.base import (
    IngestionResult,
    LocalFilesystemRawSourceStore,
    RawSourceStore,
)
from app.coverage import published_recorded_event_on
from app.governance import LicenseInput, register_release_license
from app.models import (
    Claim,
    ClaimAssertionStatus,
    DataStatus,
    DateRole,
    LegalReviewStatus,
    PipelineRun,
    QualityCheck,
    RawSourceRecord,
    ReviewTask,
    Source,
    SourceRelease,
    TemporalAssignment,
    TemporalPrecision,
)
from app.services import (
    canonical_json_bytes,
    content_hash,
    create_claim,
    create_source_release,
)

__all__ = [
    "LocalFilesystemRawSourceStore",
    "WikidataEnrichmentOutcome",
    "attempt_wikidata_enrichment",
]

WIKIDATA_SOURCE_SLUG = "wikidata-candidates"
ENTITY_ID = "Q749610"
REVISION_ID = 2497659168
ENTITY_URL = (
    "https://www.wikidata.org/wiki/Special:EntityData/"
    f"{ENTITY_ID}.json?revision={REVISION_ID}"
)


def _value(statement: dict[str, Any]) -> Any:
    mainsnak = statement.get("mainsnak")
    if not isinstance(mainsnak, dict):
        raise ValueError("Wikidata statement has no mainsnak.")
    datavalue = mainsnak.get("datavalue")
    if not isinstance(datavalue, dict) or "value" not in datavalue:
        raise ValueError("Wikidata statement has no data value.")
    return datavalue["value"]


def _first(entity: dict[str, Any], property_id: str) -> dict[str, Any]:
    claims = entity.get("claims")
    if not isinstance(claims, dict):
        raise ValueError("Wikidata entity has no claims object.")
    statements = claims.get(property_id)
    if not isinstance(statements, list) or not statements:
        raise ValueError(f"Wikidata entity is missing {property_id}.")
    statement = statements[0]
    if not isinstance(statement, dict):
        raise ValueError(f"Wikidata {property_id} statement is malformed.")
    return statement


def _reference_count(statement: dict[str, Any]) -> int:
    references = statement.get("references")
    return len(references) if isinstance(references, list) else 0


def _parse(payload: bytes) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    document: Any = json.loads(payload)
    if not isinstance(document, dict):
        raise ValueError("Wikidata fixture must be a JSON object.")
    entities = document.get("entities")
    if not isinstance(entities, dict):
        raise ValueError("Wikidata fixture has no entities map.")
    entity = entities.get(ENTITY_ID)
    if not isinstance(entity, dict) or entity.get("id") != ENTITY_ID:
        raise ValueError(f"Wikidata fixture must contain {ENTITY_ID}.")
    pageid = entity.get("pageid")
    lastrevid = entity.get("lastrevid")
    if not isinstance(pageid, int) or lastrevid != REVISION_ID:
        raise ValueError("Wikidata fixture is not the pinned entity revision.")
    labels = entity.get("labels")
    aliases = entity.get("aliases")
    if not isinstance(labels, dict) or not isinstance(aliases, dict):
        raise ValueError("Wikidata entity labels or aliases are malformed.")
    english_label = labels.get("en")
    if not isinstance(english_label, dict):
        raise ValueError("Wikidata candidate requires an English label.")
    alias_values = aliases.get("en", [])
    if not isinstance(alias_values, list):
        raise ValueError("Wikidata English aliases are malformed.")
    time_statement = _first(entity, "P585")
    time_value = _value(time_statement)
    if not isinstance(time_value, dict):
        raise ValueError("Wikidata point-in-time value is malformed.")
    coordinates_statement = _first(entity, "P625")
    coordinate_value = _value(coordinates_statement)
    if not isinstance(coordinate_value, dict):
        raise ValueError("Wikidata coordinate value is malformed.")
    candidates = (
        {
            "predicate": "candidate_event_identity",
            "value": {
                "entity_id": ENTITY_ID,
                "pageid": pageid,
                "revision_id": lastrevid,
            },
            "references": 0,
        },
        {
            "predicate": "candidate_name",
            "value": {
                "label": english_label.get("value"),
                "aliases": [
                    alias.get("value")
                    for alias in alias_values
                    if isinstance(alias, dict)
                ],
            },
            "references": 0,
        },
        {
            "predicate": "candidate_event_type",
            "value": _value(_first(entity, "P31")),
            "references": _reference_count(_first(entity, "P31")),
        },
        {
            "predicate": "candidate_occurrence_date",
            "value": time_value,
            "references": _reference_count(time_statement),
        },
        {
            "predicate": "candidate_coordinates",
            "value": coordinate_value,
            "references": _reference_count(coordinates_statement),
        },
        {
            "predicate": "candidate_magnitude",
            "value": _value(_first(entity, "P2527")),
            "references": _reference_count(_first(entity, "P2527")),
        },
        {
            "predicate": "candidate_depth",
            "value": _value(_first(entity, "P4511")),
            "references": _reference_count(_first(entity, "P4511")),
        },
        {
            "predicate": "candidate_fatalities",
            "value": _value(_first(entity, "P1120")),
            "references": _reference_count(_first(entity, "P1120")),
        },
    )
    return entity, candidates


def _license(session: Session, release_id: UUID) -> None:
    register_release_license(
        session,
        source_release_id=release_id,
        license_input=LicenseInput(
            license_identifier="CC0-1.0",
            license_snapshot=(
                "Wikidata states that structured data in the main, Property, "
                "Lexeme, and EntitySchema namespaces is available under CC0."
            ),
            terms_url="https://www.wikidata.org/wiki/Wikidata:Licensing",
            commercial_use_permission=True,
            redistribution_permission=True,
            derivatives_permission=True,
            attribution_required=False,
            attribution_text=(
                "Wikidata Q749610 revision 2497659168; attribution retained as "
                "provenance even though CC0 does not require it."
            ),
            public_display_permission=True,
            raw_download_permission=True,
            terms_checked_at=date(2026, 7, 24),
            legal_review_status=LegalReviewStatus.NOT_REQUIRED,
        ),
    )


def ingest_wikidata_candidate(
    session: Session,
    *,
    fixture_path: Path,
    raw_store: RawSourceStore,
    dry_run: bool = False,
) -> IngestionResult:
    payload = fixture_path.read_bytes()
    checksum = hashlib.sha256(payload).hexdigest()
    run = PipelineRun(
        pipeline_name="wikidata-candidate-adapter",
        code_version="0.3.0",
        configuration_hash=content_hash(
            {
                "entity": ENTITY_ID,
                "revision": REVISION_ID,
                "fixture": True,
                "dry_run": dry_run,
            }
        ),
        status="running",
        details={"mode": "fixture", "dry_run": dry_run},
    )
    session.add(run)
    session.flush()
    try:
        with session.begin_nested():
            entity, candidates = _parse(payload)
            if dry_run:
                session.add(
                    QualityCheck(
                        pipeline_run_id=run.id,
                        check_name="wikidata_q749610_schema",
                        status="passed",
                        subject_type="pipeline_run",
                        subject_id=run.id,
                        details={
                            "candidate_count": len(candidates),
                            "persisted": False,
                        },
                    )
                )
                run.status = "succeeded"
                run.completed_at = datetime.now(UTC)
                run.details = {**run.details, "validated_only": True}
                return IngestionResult(
                    run.id,
                    None,
                    (),
                    checksum,
                    checksum,
                    False,
                    True,
                )
            source = session.scalar(
                select(Source).where(Source.slug == WIKIDATA_SOURCE_SLUG)
            )
            if source is None:
                source = Source(
                    slug=WIKIDATA_SOURCE_SLUG,
                    name="Wikidata candidate entities",
                    publisher="Wikimedia Foundation and Wikidata contributors",
                    canonical_url="https://www.wikidata.org/",
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
                _license(session, existing.id)
                run.status = "succeeded"
                run.completed_at = datetime.now(UTC)
                run.details = {**run.details, "idempotent": True}
                existing_claim_ids = tuple(
                    session.scalars(
                        select(Claim.id).where(
                            Claim.source_release_id == existing.id
                        )
                    )
                )
                return IngestionResult(
                    run.id,
                    existing.id,
                    existing_claim_ids,
                    checksum,
                    checksum,
                    True,
                    False,
                )
            storage_uri = raw_store.write(WIKIDATA_SOURCE_SLUG, checksum, payload)
            release = create_source_release(
                session,
                source_id=source.id,
                release_label=f"wikidata-{ENTITY_ID}-revision-{REVISION_ID}",
                source_url=ENTITY_URL,
                raw_storage_uri=storage_uri,
                raw_bytes=payload,
                raw_record_count=1,
                pipeline_run_id=run.id,
                metadata_json={
                    "entity_id": ENTITY_ID,
                    "quality_contract_version": "1",
                    "required_quality_checks": ["wikidata_q749610_schema"],
                    "revision_id": REVISION_ID,
                    "fixture": "official pinned entity JSON",
                    "license": "CC0-1.0",
                    "candidate_only": True,
                },
                legal_review_status=LegalReviewStatus.NOT_REQUIRED,
            )
            _license(session, release.id)
            record_hash = hashlib.sha256(canonical_json_bytes(entity)).hexdigest()
            locator = f"https://www.wikidata.org/wiki/{ENTITY_ID}?oldid={REVISION_ID}"
            session.add(
                RawSourceRecord(
                    source_release_id=release.id,
                    source_record_id=ENTITY_ID,
                    source_record_locator=locator,
                    raw_storage_uri=storage_uri,
                    raw_checksum_sha256=record_hash,
                    schema_version="wikidata-entity-json-v1",
                    payload_json=entity,
                )
            )
            new_claim_ids: list[UUID] = []
            for candidate in candidates:
                predicate = str(candidate["predicate"])
                value = candidate["value"]
                references = int(candidate["references"])
                assertion_json = {
                    "value": value,
                    "wikidata_reference_count": references,
                    "candidate_only": True,
                }
                claim = create_claim(
                    session,
                    source_release_id=release.id,
                    source_record_locator=locator,
                    source_record_hash_sha256=record_hash,
                    claim_type=predicate,
                    assertion_text=json.dumps(value, sort_keys=True),
                    assertion_json=assertion_json,
                    assertion_status=ClaimAssertionStatus.CANDIDATE,
                )
                claim.temporal_start = date(1964, 3, 27)
                claim.temporal_end = date(1964, 3, 27)
                claim.temporal_precision = TemporalPrecision.DAY
                claim.temporal_assignment = TemporalAssignment.REPORTED
                claim.date_role = DateRole.OCCURRED
                claim.data_status = DataStatus.REPORTED
                claim.pipeline_run_id = run.id
                new_claim_ids.append(claim.id)
                session.add(
                    ReviewTask(
                        claim_id=claim.id,
                        status="open",
                        priority=(
                            "high"
                            if predicate == "candidate_fatalities" and references == 0
                            else "normal"
                        ),
                        rationale=(
                            "Candidate only. Verify source independence, references, "
                            "and duplication before acceptance."
                        ),
                    )
                )
            session.add(
                QualityCheck(
                    pipeline_run_id=run.id,
                    check_name="wikidata_q749610_schema",
                    status="passed",
                    subject_type="source_release",
                    subject_id=release.id,
                    details={
                        "candidate_count": len(candidates),
                        "unreferenced_fatality_candidate": True,
                        "candidate_only": True,
                    },
                )
            )
            run.status = "succeeded"
            run.completed_at = datetime.now(UTC)
            run.details = {**run.details, "idempotent": False}
            return IngestionResult(
                run.id,
                release.id,
                tuple(new_claim_ids),
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
                check_name="wikidata_q749610_schema",
                status="failed",
                subject_type="pipeline_run",
                subject_id=run.id,
                details={"error": str(error)},
            )
        )
        session.flush()
        raise


@dataclass(frozen=True)
class WikidataEnrichmentOutcome:
    """The result of one enrichment collision check.

    ``no_collision`` means the candidate's date holds no published recorded event
    and is clear to enrich (a later slice publishes it). ``deferred_to_merge_review``
    means the date already publishes a recorded event, so enrichment defers -- a
    later slice must not publish a competing one until a human decides
    merge/supersede/distinct-event.
    """

    status: str
    occurrence_date: date
    colliding_manifest_id: UUID | None = None


def attempt_wikidata_enrichment(session: Session) -> WikidataEnrichmentOutcome:
    """Report whether the ingested Wikidata candidate collides with a recorded event.

    A pure detector: it reads the candidate's occurrence date and reports whether
    that date already publishes a recorded event, so a later slice never publishes
    a competing one. It writes nothing -- no Event, resolution, review task,
    editorial decision, manifest, or profile (D038: a pass never overrules a
    human, and here it does not even record on their behalf). The durable,
    resolvable merge-review record is a G2 concern, designed alongside the
    merge/supersede/distinct-event lifecycle; the G2 publish path calls
    ``published_recorded_event_on`` -- exactly as this does -- before publishing.
    """
    source = session.scalar(
        select(Source).where(Source.slug == WIKIDATA_SOURCE_SLUG)
    )
    release = (
        session.scalars(
            select(SourceRelease)
            .where(SourceRelease.source_id == source.id)
            .order_by(SourceRelease.ingested_at.desc())
        ).first()
        if source is not None
        else None
    )
    if release is None:
        raise ValueError("Wikidata candidate has not been ingested.")

    occurrence = session.scalar(
        select(Claim).where(
            Claim.source_release_id == release.id,
            Claim.claim_type == "candidate_occurrence_date",
        )
    )
    if occurrence is None:
        raise ValueError("Wikidata candidate is missing its occurrence date.")
    if occurrence.date_role is not DateRole.OCCURRED or occurrence.temporal_start is None:
        raise ValueError("Wikidata candidate has no resolved occurrence date.")
    occurrence_date = occurrence.temporal_start

    manifest = published_recorded_event_on(session, occurrence_date)
    if manifest is None:
        return WikidataEnrichmentOutcome(
            status="no_collision", occurrence_date=occurrence_date
        )
    return WikidataEnrichmentOutcome(
        status="deferred_to_merge_review",
        occurrence_date=occurrence_date,
        colliding_manifest_id=manifest.id,
    )
