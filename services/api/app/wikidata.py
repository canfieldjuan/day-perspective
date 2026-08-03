from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from geoalchemy2.elements import WKTElement
from sqlalchemy import func, select
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
    Event,
    EventLocation,
    EventTime,
    LegalReviewStatus,
    Methodology,
    PipelineRun,
    QualityCheck,
    RawSourceRecord,
    ResolutionMethod,
    ResolvedClaim,
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
    resolve_claim,
)

__all__ = [
    "LocalFilesystemRawSourceStore",
    "WikidataEnrichmentOutcome",
    "attempt_wikidata_enrichment",
    "resolve_wikidata_event",
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


#: The candidate predicates a resolved Wikidata event requires: who/what it is,
#: what kind, its name, and when. Coordinates are resolved too when present, but
#: an event without a location is still a resolvable event.
REQUIRED_EVENT_CLAIMS = (
    "candidate_event_identity",
    "candidate_event_type",
    "candidate_name",
    "candidate_occurrence_date",
)


def _wikidata_methodology(session: Session) -> Methodology:
    existing = session.scalar(
        select(Methodology).where(
            Methodology.slug == "wikidata-single-candidate",
            Methodology.version == "1",
        )
    )
    if existing is not None:
        return existing
    definition = {
        "authority": "Wikidata contributors (Wikimedia Foundation)",
        "resolution": (
            "Accept one reviewed Wikidata candidate per predicate; single-source "
            "acceptance, not independent corroboration."
        ),
    }
    row = Methodology(
        slug="wikidata-single-candidate",
        version="1",
        name="Wikidata single-candidate resolution",
        description=definition["resolution"],
        method_kind="single_source_resolution",
        formula=None,
        code_version="0.1.0",
        definition_hash=hashlib.sha256(canonical_json_bytes(definition)).hexdigest(),
        legal_review_status=LegalReviewStatus.NOT_REQUIRED,
    )
    session.add(row)
    session.flush()
    return row


def _candidate_value(claim: Claim) -> dict[str, Any]:
    value = (claim.assertion_json or {}).get("value")
    return value if isinstance(value, dict) else {}


def _parse_occurrence_date(value: dict[str, Any]) -> date:
    """The P585 day, or an error when the value is not day-precise.

    Wikidata precision 11 is a day; anything coarser cannot place the event on a
    specific date, and this arc is date-specific enrichment. The date is parsed
    from the P585 value itself, not from a stamped column, so it is honest for
    any entity.
    """
    time_text = value.get("time")
    if not isinstance(time_text, str) or value.get("precision") != 11:
        raise ValueError(
            "Wikidata occurrence date is not day-precise (expected P585 precision 11)."
        )
    return date.fromisoformat(time_text.lstrip("+")[:10])


def _resolve_candidate(
    session: Session, *, qid: str, claim: Claim, methodology: Methodology
) -> ResolvedClaim:
    return resolve_claim(
        session,
        canonical_key=f"wikidata:{qid}:{claim.claim_type}",
        resolved_value=claim.assertion_json or {"text": claim.assertion_text},
        rationale=(
            "Accepted one reviewed Wikidata candidate; single-source discovery, "
            "not independent corroboration."
        ),
        supporting_claim_ids=[claim.id],
        resolution_method=ResolutionMethod.SINGLE_SOURCE,
        methodology_id=methodology.id,
    )


def _ensure_event_location(
    session: Session,
    *,
    event: Event,
    coordinates: Claim | None,
    qid: str,
    methodology: Methodology,
) -> None:
    """Attach the P625 point when coordinates are accepted, at most once.

    Runs on every resolve so coordinates accepted *after* the event was first
    resolved still attach -- claims are reviewed independently. Idempotent: a
    location already present is left untouched, and an existing resolved
    coordinate claim is reused rather than re-resolved.
    """
    already = session.scalar(
        select(EventLocation).where(EventLocation.event_id == event.id)
    )
    if already is not None:
        return
    if (
        coordinates is None
        or coordinates.assertion_status is not ClaimAssertionStatus.ACCEPTED
    ):
        return
    value = _candidate_value(coordinates)
    latitude = value.get("latitude")
    longitude = value.get("longitude")
    if not (isinstance(latitude, int | float) and isinstance(longitude, int | float)):
        return
    resolved = session.scalar(
        select(ResolvedClaim)
        .where(
            ResolvedClaim.canonical_key == f"wikidata:{qid}:candidate_coordinates"
        )
        .order_by(ResolvedClaim.version.desc())
    )
    if resolved is None:
        resolved = _resolve_candidate(
            session, qid=qid, claim=coordinates, methodology=methodology
        )
    session.add(
        EventLocation(
            event_id=event.id,
            geography_version_id=None,
            provenance_resolved_claim_id=resolved.id,
            point_geometry=WKTElement(f"POINT({longitude} {latitude})", srid=4326),
            location_role="primary",
        )
    )
    session.flush()


def resolve_wikidata_event(session: Session) -> Event:
    """Turn the reviewed Wikidata candidate into a canonical Event.

    Requires the core candidate claims to be ACCEPTED first -- D019: Wikidata is
    candidate discovery, not confirmation, so the resolver never accepts on a
    human's behalf. It resolves identity/type/name/occurrence (and coordinates,
    when present) into versioned resolved claims and builds the Event, its primary
    EventTime (day-precision, occurred, and REPORTED -- a secondary source, not a
    direct record), and, when coordinates are present, a bare-point EventLocation
    with no invented named region. Idempotent: a second call returns the
    already-resolved Event. It does not publish; publishing a Wikidata recorded
    event is a later slice, gated by ``published_recorded_event_on``.
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

    claims = {
        claim.claim_type: claim
        for claim in session.scalars(
            select(Claim).where(Claim.source_release_id == release.id)
        )
    }
    for claim_type in REQUIRED_EVENT_CLAIMS:
        claim = claims.get(claim_type)
        if claim is None:
            raise ValueError(f"Wikidata candidate is missing {claim_type}.")
        if claim.assertion_status is not ClaimAssertionStatus.ACCEPTED:
            raise ValueError(
                "Wikidata candidates must be human-reviewed and accepted before "
                f"resolution ({claim_type} is {claim.assertion_status.value})."
            )

    qid = _candidate_value(claims["candidate_event_identity"]).get("entity_id")
    if not isinstance(qid, str):
        raise ValueError("Wikidata identity candidate has no entity id.")

    # Validate the occurrence date before any write: a bad P585 must not leave a
    # half-resolved identity behind. The CLI commits its audit trail on failure,
    # which would otherwise persist an identity with no event and wedge retries
    # (the next call finds the identity, no event, and re-resolves without a
    # supersession id).
    occurrence_date = _parse_occurrence_date(
        _candidate_value(claims["candidate_occurrence_date"])
    )

    # Serialize per entity so two concurrent resolves cannot double-create the
    # event or its location (the governance writers' advisory-lock pattern).
    session.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtextextended(f"wikidata-resolve:{qid}", 0)
            )
        )
    )

    methodology = _wikidata_methodology(session)

    # Idempotent on the required core: an already-resolved entity reuses its
    # event rather than minting a second version of every resolved claim.
    existing_identity = session.scalar(
        select(ResolvedClaim)
        .where(
            ResolvedClaim.canonical_key
            == f"wikidata:{qid}:candidate_event_identity"
        )
        .order_by(ResolvedClaim.version.desc())
    )
    event = (
        session.scalar(
            select(Event).where(Event.resolved_claim_id == existing_identity.id)
        )
        if existing_identity is not None
        else None
    )

    if event is None:
        resolved = {
            claim_type: _resolve_candidate(
                session, qid=qid, claim=claims[claim_type], methodology=methodology
            )
            for claim_type in REQUIRED_EVENT_CLAIMS
        }
        event = Event(
            resolved_claim_id=resolved["candidate_event_identity"].id,
            event_type=str(
                _candidate_value(claims["candidate_event_type"]).get("id", "")
            ),
            canonical_title=str(
                _candidate_value(claims["candidate_name"]).get("label", "")
            ),
            summary=None,
            data_status=DataStatus.REPORTED,
        )
        session.add(event)
        session.flush()
        session.add(
            EventTime(
                event_id=event.id,
                provenance_resolved_claim_id=resolved["candidate_occurrence_date"].id,
                start_date=occurrence_date,
                end_date=occurrence_date,
                temporal_precision=TemporalPrecision.DAY,
                temporal_assignment=TemporalAssignment.REPORTED,
                date_role=DateRole.OCCURRED,
                is_primary=True,
            )
        )

    # Convergent: reconcile the (optional) location every call, so coordinates
    # accepted after the event was first resolved still attach.
    _ensure_event_location(
        session,
        event=event,
        coordinates=claims.get("candidate_coordinates"),
        qid=qid,
        methodology=methodology,
    )
    session.flush()
    return event
