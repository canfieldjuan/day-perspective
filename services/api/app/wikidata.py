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
from app.governance import (
    EditorialSelection,
    EditorialSelectionStatus,
    EventIdentityAdjudication,
    IdentityAdjudicationDecision,
    LicenseInput,
    assert_release_publication_eligible,
    current_featured_selection,
    events_behind_manifest,
    is_human_reviewer,
    latest_identity_adjudication,
    record_identity_adjudication,
    record_published_events,
    register_release_license,
    resolve_featured_event,
)
from app.models import (
    Claim,
    ClaimAssertionStatus,
    DataStatus,
    DateRole,
    DayProfile,
    Event,
    EventLocation,
    EventTime,
    LegalReviewStatus,
    Methodology,
    PipelineRun,
    PublicationManifest,
    PublicationStatementEvidence,
    PublicationStatus,
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
    profile_type_for_date,
)
from app.services import (
    PublicationStatementEvidenceInput,
    PublishedProfileStore,
    canonical_json_bytes,
    content_hash,
    create_claim,
    create_source_release,
    publish_day_profile,
    resolve_claim,
)

__all__ = [
    "LocalFilesystemRawSourceStore",
    "WikidataEnrichmentOutcome",
    "WikidataPublishOutcome",
    "attempt_wikidata_enrichment",
    "publish_wikidata_event",
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


@dataclass(frozen=True)
class WikidataPublishOutcome:
    """The result of one publish attempt for the reviewed Wikidata candidate.

    ``published`` means the resolved candidate was published as the date's recorded
    event (``manifest_id`` / ``day_profile_id`` set). ``deferred_to_merge_review``
    means the occurrence date already publishes a recorded event and nobody has
    adjudicated the pair yet, so the pass published nothing and opened (or reused)
    a durable merge-review task (``merge_review_task_id`` /
    ``colliding_manifest_id`` set) for a human to decide merge, supersede, or
    distinct-event. ``blocked_by_adjudication`` means a human has already decided,
    and their decision was not ``distinct_event``: the collision stands, and
    ``adjudication_id`` points at the decision that says so rather than asking the
    same question again with a fresh review task.
    """

    status: str
    occurrence_date: date
    manifest_id: UUID | None = None
    day_profile_id: UUID | None = None
    colliding_manifest_id: UUID | None = None
    merge_review_task_id: UUID | None = None
    adjudication_id: UUID | None = None


#: The reviewed predicates that become recorded-event statements, each paired with
#: the honest, data-derived text it renders. Identity is provenance, not a
#: reader-facing statement; magnitude/depth/fatalities resolution is a later slice.
_PUBLISHED_PREDICATES = (
    "candidate_name",
    "candidate_event_type",
    "candidate_occurrence_date",
    "candidate_coordinates",
)


def _latest_resolved(
    session: Session, *, qid: str, predicate: str
) -> ResolvedClaim | None:
    return session.scalar(
        select(ResolvedClaim)
        .where(ResolvedClaim.canonical_key == f"wikidata:{qid}:{predicate}")
        .order_by(ResolvedClaim.version.desc())
    )


def _manifest_is_wikidata_event(
    session: Session, *, manifest: PublicationManifest, qid: str
) -> bool:
    """Whether a published manifest is this Wikidata entity's own recorded event.

    True when the manifest's statement evidence rests on a ``wikidata:{qid}:*``
    resolved claim -- so re-publishing our own event is idempotent, while a
    recorded event published from any other source (e.g. the USGS golden profile,
    keyed ``usgs:*``) is a genuine collision that must defer.
    """
    prefix = f"wikidata:{qid}:"
    keys = session.scalars(
        select(ResolvedClaim.canonical_key)
        .join(
            PublicationStatementEvidence,
            PublicationStatementEvidence.resolved_claim_id == ResolvedClaim.id,
        )
        .where(PublicationStatementEvidence.publication_manifest_id == manifest.id)
    )
    return any(isinstance(key, str) and key.startswith(prefix) for key in keys)


def _merge_review_tasks(
    session: Session, *, identity_claim: Claim, active_only: bool
) -> list[ReviewTask]:
    """This candidate's merge-review tasks, newest last."""
    statement = select(ReviewTask).where(
        ReviewTask.claim_id == identity_claim.id,
        ReviewTask.rationale.like("MERGE-REVIEW:%"),
    )
    if active_only:
        # A reviewer who has claimed the task moved it to ``in_progress``;
        # treating only ``open`` as active would open a second task behind their
        # back, which is how a queue grows duplicates of the same question.
        # Every other review-task consumer treats both as active.
        statement = statement.where(ReviewTask.status.in_(("open", "in_progress")))
    return list(session.scalars(statement.order_by(ReviewTask.created_at.asc())))


def _task_concerns(
    session: Session, *, task: ReviewTask, colliding_events: set[UUID]
) -> bool:
    """Whether a merge-review task is about this exact collision.

    The one rule every path that opens, reuses, or acts on a merge-review task
    has to agree on: a task asks about one collision, identified by the events
    behind it. Compared on events rather than manifest identity because
    republishing the same recorded event mints a new manifest, and treating that
    as a different question would strand the reviewer.

    A task with no recorded subject cannot be shown to concern anything, so it
    answers False -- absence is not permission.
    """
    if task.context_manifest_id is None:
        return False
    asked_about = session.get(PublicationManifest, task.context_manifest_id)
    if asked_about is None:
        return False
    return events_behind_manifest(session, manifest=asked_about) == colliding_events


def _ensure_merge_review_task(
    session: Session,
    *,
    identity_claim: Claim,
    qid: str,
    occurrence_date: date,
    colliding_manifest_id: UUID,
    colliding_events: set[UUID],
) -> ReviewTask:
    """Open (or reuse) the durable merge-review task for a recorded-event collision.

    Idempotent for the *same* collision, so repeated publish attempts never stack
    duplicate tasks. A task whose collision has moved on is retired rather than
    reused: resolution refuses a stale subject, so handing one back on every
    attempt would leave the candidate in a publish/reject loop with no way out
    but editing the database.

    The pass records no decision on a human's behalf (D038): the task asks a
    human to choose merge, supersede, or distinct-event before any competing
    recorded event is published.
    """
    for existing in _merge_review_tasks(
        session, identity_claim=identity_claim, active_only=True
    ):
        if _task_concerns(
            session, task=existing, colliding_events=colliding_events
        ):
            return existing
        existing.status = "dismissed"
        existing.completed_at = datetime.now(UTC)
        existing.rationale = (
            f"{existing.rationale} SUPERSEDED: the date now publishes a "
            "different recorded event, so this question no longer describes the "
            "collision; a task for the current collision replaces it."
        )
    session.flush()
    task = ReviewTask(
        claim_id=identity_claim.id,
        # Bind the question to the publication it is about, structurally rather
        # than in prose, so the answer cannot land on a different collision.
        context_manifest_id=colliding_manifest_id,
        status="open",
        priority="high",
        rationale=(
            f"MERGE-REVIEW: Wikidata {qid} occurs on {occurrence_date.isoformat()}, "
            f"which already publishes recorded event {colliding_manifest_id}. A human "
            "must decide merge, supersede, or distinct-event before publishing a "
            "competing recorded event."
        ),
    )
    session.add(task)
    session.flush()
    return task


def _latest_release(session: Session) -> SourceRelease:
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
    return release


def _identity_claim(session: Session) -> Claim:
    release = _latest_release(session)
    claim = session.scalar(
        select(Claim).where(
            Claim.source_release_id == release.id,
            Claim.claim_type == "candidate_event_identity",
        )
    )
    if claim is None:
        raise ValueError("Wikidata candidate is missing candidate_event_identity.")
    return claim


def _resolved_entity(session: Session) -> tuple[str, Event | None]:
    """The candidate's entity id and its resolved Event, when one exists yet."""
    qid = _candidate_value(_identity_claim(session)).get("entity_id")
    if not isinstance(qid, str):
        raise ValueError("Wikidata identity candidate has no entity id.")
    identity_resolved = _latest_resolved(
        session, qid=qid, predicate="candidate_event_identity"
    )
    event = (
        session.scalar(
            select(Event).where(Event.resolved_claim_id == identity_resolved.id)
        )
        if identity_resolved is not None
        else None
    )
    return qid, event


def _collision_adjudication(
    session: Session, *, event: Event | None, manifest: PublicationManifest
) -> tuple[bool, EventIdentityAdjudication | None]:
    """Whether this event may publish past a colliding manifest, and what says so.

    Returns ``(bypass, blocking_decision)``. A bypass requires a *current human*
    ``distinct_event`` decision against every event the colliding manifest
    publishes: one unadjudicated event on that manifest is enough to keep
    deferring, so a decision about one pair never becomes blanket permission for
    the date.

    Fail-closed in both directions that matter. An unresolved candidate has no
    event to adjudicate, and a manifest whose events cannot be resolved from its
    evidence yields no decisions at all -- which would make "every event is
    adjudicated distinct" vacuously true over an empty set, and bypass the guard
    precisely when the collision is least understood.
    """
    if event is None:
        return False, None
    others = events_behind_manifest(session, manifest=manifest)
    if not others:
        return False, None
    decisions = [
        latest_identity_adjudication(
            session, event_a_id=event.id, event_b_id=other_id
        )
        for other_id in sorted(others, key=str)
    ]

    def _permits(decision: EventIdentityAdjudication | None) -> bool:
        return (
            decision is not None
            and decision.decision
            == IdentityAdjudicationDecision.DISTINCT_EVENT.value
            and is_human_reviewer(decision.reviewer)
        )

    if all(_permits(decision) for decision in decisions):
        return True, None
    # A recorded decision that does not permit publication is an answer, not an
    # open question: surface it instead of opening another review task asking a
    # human what they already told us.
    blocking = next(
        (
            decision
            for decision in decisions
            if decision is not None and not _permits(decision)
        ),
        None,
    )
    return False, blocking


def resolve_merge_review(
    session: Session,
    *,
    decision: IdentityAdjudicationDecision,
    reviewer: str,
    rationale: str,
    survivor_event_id: UUID | None = None,
) -> tuple[EventIdentityAdjudication, ...]:
    """Record a human's merge-review answer as the durable decision, and close it.

    This is what the ``MERGE-REVIEW:`` review task is *for*. Closing that task
    used to be the whole workflow, which meant the answer went nowhere: the guard
    could not read it, so the next publish attempt collided and opened the task
    again. Resolving through here writes the pair-specific adjudication the guard
    consumes, links it to the task that asked, and then marks the task resolved.
    """
    qid, event = _resolved_entity(session)
    if event is None:
        raise ValueError(
            "The Wikidata candidate must be resolved into an event before its "
            "identity can be adjudicated."
        )
    event_time = session.scalar(
        select(EventTime).where(
            EventTime.event_id == event.id, EventTime.is_primary.is_(True)
        )
    )
    if event_time is None:
        raise ValueError("The resolved event has no primary occurrence time.")
    collision = published_recorded_event_on(session, event_time.start_date)
    if collision is None or _manifest_is_wikidata_event(
        session, manifest=collision, qid=qid
    ):
        raise ValueError(
            "There is no recorded-event collision on "
            f"{event_time.start_date.isoformat()} to adjudicate."
        )
    others = events_behind_manifest(session, manifest=collision)
    if not others:
        raise ValueError(
            "The colliding manifest's recorded event cannot be resolved to a "
            "canonical event, so the pair cannot be adjudicated."
        )
    identity_claim = _identity_claim(session)
    # The reviewer answers about the pair a task showed them, so a task about
    # this exact collision has to exist. Resolved tasks count: the first answer
    # closes the task, and a retry of the same command must be checked against
    # the same subject rather than sail past the guard because nothing is open.
    # Otherwise repeating the command after the date was republished records the
    # same answer against the new pair -- a durable bypass for events nobody
    # evaluated, reached by pressing up and enter.
    concerning = [
        candidate
        for candidate in _merge_review_tasks(
            session, identity_claim=identity_claim, active_only=False
        )
        if _task_concerns(session, task=candidate, colliding_events=others)
    ]
    if not concerning:
        raise ValueError(
            "The recorded-event collision on "
            f"{event_time.start_date.isoformat()} is no longer the one any "
            "merge-review task was opened for; the date publishes a different "
            "event, or no review was raised for this collision. Re-run the "
            "publish attempt to raise the review against the current collision "
            "rather than record a decision about a pair nobody evaluated."
        )
    # Prefer an active task so the answer closes the question that is still open.
    task = next(
        (
            candidate
            for candidate in concerning
            if candidate.status in ("open", "in_progress")
        ),
        concerning[-1],
    )

    recorded = tuple(
        record_identity_adjudication(
            session,
            event_a_id=event.id,
            event_b_id=other_id,
            decision=decision,
            reviewer=reviewer,
            rationale=rationale,
            survivor_event_id=survivor_event_id,
            # Resolving the same collision twice reuses the task that asked, so
            # a retry reconstructs identical arguments and the writer returns the
            # existing row instead of appending a version of the same decision.
            review_task_id=task.id,
        )
        for other_id in sorted(others, key=str)
    )
    if task.status in ("open", "in_progress"):
        task.status = "resolved"
        task.completed_at = datetime.now(UTC)
    session.flush()
    return recorded


def _wikidata_statement_provenance(
    *,
    claim: Claim,
    resolved: ResolvedClaim,
    release: SourceRelease,
    source: Source,
    methodology: Methodology,
) -> dict[str, Any]:
    return {
        "root_type": "resolved_claim",
        "published_statement": (
            "This statement is selected for the recorded-event section."
        ),
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


def _resolved_value(resolved: ResolvedClaim) -> dict[str, Any]:
    """The candidate value captured in a resolved claim at resolution time.

    Read from the resolution snapshot, not the live claim, so published content
    reflects what was actually resolved even if the source record later changes.
    """
    value = (resolved.resolved_value or {}).get("value")
    return value if isinstance(value, dict) else {}


def _resolution_lineage(
    session: Session, *, resolved: ResolvedClaim
) -> tuple[Claim, SourceRelease]:
    """The claim and release a resolution actually rests on (its evidence lineage).

    Provenance must cite the source record that supports the resolved value, not
    whatever release is newest -- otherwise a re-ingest could make a published
    statement claim a new record supports an old resolution.
    """
    claim = session.scalar(
        select(Claim)
        .join(ResolvedClaimEvidence, ResolvedClaimEvidence.claim_id == Claim.id)
        .where(
            ResolvedClaimEvidence.resolved_claim_id == resolved.id,
            ResolvedClaimEvidence.stance == "supporting",
        )
        .order_by(Claim.id)
    )
    if claim is None:
        raise ValueError(
            f"Resolved claim {resolved.canonical_key} has no supporting claim lineage."
        )
    release = session.get(SourceRelease, claim.source_release_id)
    if release is None:
        raise ValueError(
            f"Resolved claim {resolved.canonical_key} has no source release lineage."
        )
    return claim, release


def _recorded_statement_text(predicate: str, *, value: dict[str, Any]) -> str:
    """Honest, data-derived statement text for one recorded predicate.

    Every value is read from the resolved candidate; nothing is invented (§12).
    """
    if predicate == "candidate_name":
        return f'Wikidata records this event as "{value.get("label", "")}".'
    if predicate == "candidate_event_type":
        return f"Wikidata classifies the entity as type {value.get('id', '')}."
    if predicate == "candidate_occurrence_date":
        occurrence = _parse_occurrence_date(value)
        return (
            "Wikidata records the occurrence on "
            f"{occurrence:%B} {occurrence.day}, {occurrence.year}."
        )
    if predicate == "candidate_coordinates":
        return (
            "Wikidata places the event at "
            f"{value.get('latitude')} latitude, {value.get('longitude')} longitude."
        )
    raise ValueError(f"No recorded-statement rendering for {predicate}.")


def _ranked_recorded_predicates(
    session: Session, *, qid: str, profile_date: date
) -> list[tuple[str, ResolvedClaim]]:
    """This entity's recorded predicates, in the human editorial-ranking order.

    Drives *what* is published and *in what order* from the human editorial
    selections, not the source predicate order -- so the editorial-ranking stage
    the pass consumes actually decides the reader-visible order. Only resolved
    predicates a human selected (latest decision ``SELECTED``) are returned;
    unranked selections sort last, stably, behind ranked ones.
    """
    latest: dict[UUID, EditorialSelection] = {}
    for selection in session.scalars(
        select(EditorialSelection)
        .where(
            EditorialSelection.profile_date == profile_date,
            EditorialSelection.section_key == "recorded_on_this_date",
        )
        .order_by(EditorialSelection.decision_version.desc())
    ):
        if selection.resolved_claim_id is not None:
            latest.setdefault(selection.resolved_claim_id, selection)
    ranked: list[tuple[int | None, str, ResolvedClaim]] = []
    for predicate in _PUBLISHED_PREDICATES:
        resolved = _latest_resolved(session, qid=qid, predicate=predicate)
        if resolved is None:
            continue
        chosen = latest.get(resolved.id)
        if chosen is None or chosen.status != EditorialSelectionStatus.SELECTED.value:
            continue
        ranked.append((chosen.display_rank, predicate, resolved))
    ranked.sort(key=lambda item: (item[0] is None, item[0] if item[0] is not None else 0))
    return [(predicate, resolved) for _, predicate, resolved in ranked]


def _latest_recorded_selections(
    session: Session, *, profile_date: date
) -> dict[UUID, EditorialSelection]:
    """Each recorded-section root's current editorial decision for a date."""
    latest: dict[UUID, EditorialSelection] = {}
    for selection in session.scalars(
        select(EditorialSelection)
        .where(
            EditorialSelection.profile_date == profile_date,
            EditorialSelection.section_key == "recorded_on_this_date",
        )
        .order_by(EditorialSelection.decision_version.desc())
    ):
        if selection.resolved_claim_id is not None:
            latest.setdefault(selection.resolved_claim_id, selection)
    return latest


def _retained_recorded_statements(
    session: Session,
    *,
    store: PublishedProfileStore,
    manifest: PublicationManifest,
    profile_date: date,
    rebuilt_key_prefix: str,
) -> tuple[list[dict[str, Any]], list[UUID]]:
    """The prior version's recorded statements for events this pass is *not* rebuilding.

    Re-checked against the current editorial selections rather than copied. An
    artifact that preserved a predicate merely because the previous version
    carried it would keep asserting something nobody currently stands behind,
    which is the difference between an archive and a cache.

    ``rebuilt_key_prefix`` names the resolution keyspace this pass regenerates
    from scratch -- its own entity's. Retaining those too would publish every one
    of this event's statements twice on a republication, which is not a display
    quirk: it changes the content hash, so an otherwise idempotent republish
    would mint a new version every time it ran.

    Scoped to the recorded-event section deliberately; the general revalidation
    of every carried section is #78 and stays deferred.
    """
    payload = store.read(manifest.storage_uri, manifest.content_hash)
    sections = payload.get("sections")
    prior = sections.get("recorded_on_this_date") if isinstance(sections, dict) else None
    if not isinstance(prior, list):
        return [], []
    roots_by_index: dict[int, UUID] = {}
    for row in session.scalars(
        select(PublicationStatementEvidence).where(
            PublicationStatementEvidence.publication_manifest_id == manifest.id
        )
    ):
        if not row.statement_path.startswith("/sections/recorded_on_this_date/"):
            continue
        tail = row.statement_path.rsplit("/", 1)[-1]
        if tail.isdigit() and row.resolved_claim_id is not None:
            roots_by_index[int(tail)] = row.resolved_claim_id
    latest = _latest_recorded_selections(session, profile_date=profile_date)
    rebuilt = {
        root_id
        for root_id, key in session.execute(
            select(ResolvedClaim.id, ResolvedClaim.canonical_key).where(
                ResolvedClaim.id.in_(roots_by_index.values())
            )
        )
        if isinstance(key, str) and key.startswith(rebuilt_key_prefix)
    }
    statements: list[dict[str, Any]] = []
    roots: list[UUID] = []
    for index, statement in enumerate(prior):
        root = roots_by_index.get(index)
        if root is None or root in rebuilt or not isinstance(statement, dict):
            continue
        selection = latest.get(root)
        if (
            selection is None
            or selection.status != EditorialSelectionStatus.SELECTED.value
        ):
            continue
        statements.append(statement)
        roots.append(root)
    return statements, roots


def _carried_forward_evidence(
    session: Session, *, manifest_id: UUID
) -> list[PublicationStatementEvidenceInput]:
    """The prior profile's statement-evidence inputs, minus the recorded section.

    Enrichment rebuilds ``recorded_on_this_date`` fresh but must preserve every
    other section a prior profile published (annual context, comparisons), so the
    new version enriches the date rather than replacing it. Only the evidence
    *inputs* are carried; the spine re-snapshots them at publication.
    """
    carried: list[PublicationStatementEvidenceInput] = []
    for row in session.scalars(
        select(PublicationStatementEvidence).where(
            PublicationStatementEvidence.publication_manifest_id == manifest_id
        )
    ):
        if row.statement_path.startswith("/sections/recorded_on_this_date/"):
            continue
        carried.append(
            PublicationStatementEvidenceInput(
                statement_path=row.statement_path,
                resolved_claim_id=row.resolved_claim_id,
                derived_value_id=row.derived_value_id,
            )
        )
    return carried


def publish_wikidata_event(
    session: Session,
    *,
    store: PublishedProfileStore,
    force_new_version: bool = False,
) -> WikidataPublishOutcome:
    """Publish the resolved Wikidata candidate as its date's recorded event.

    Generalizes the USGS golden publisher off ``GOLDEN_DATE`` and the
    ``ProfileType`` literal: the date and profile type come from the event's own
    occurrence, and publication runs through the same source-agnostic spine
    (``publish_day_profile``) and eligibility gate. The pass consumes the human
    stages before it -- claim acceptance (D019) and editorial ranking -- and
    fabricates neither (D038); an unaccepted or unranked candidate is refused.

    Before minting anything, it checks ``published_recorded_event_on``: a date that
    already publishes a *different* recorded event defers to a durable merge-review
    task and no competing event or profile is created. Re-publishing this entity's
    own recorded event is idempotent.
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
    if source is None or release is None:
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
                f"publication ({claim_type} is {claim.assertion_status.value})."
            )

    qid = _candidate_value(claims["candidate_event_identity"]).get("entity_id")
    if not isinstance(qid, str):
        raise ValueError("Wikidata identity candidate has no entity id.")

    # The event is resolved by a prior stage (G2a). Everything the publish path
    # keys on -- the occurrence date, the collision guard, statements, and
    # provenance -- binds to that resolution, not to whatever candidate the newest
    # release carries.
    identity_resolved = _latest_resolved(
        session, qid=qid, predicate="candidate_event_identity"
    )
    event = (
        session.scalar(
            select(Event).where(Event.resolved_claim_id == identity_resolved.id)
        )
        if identity_resolved is not None
        else None
    )
    resolved_occurrence = _latest_resolved(
        session, qid=qid, predicate="candidate_occurrence_date"
    )

    # The occurrence date is the resolution's once resolved, so the collision guard
    # and the publish target are the same date -- a re-ingest that moves P585 cannot
    # let the guard miss a recorded event on the date we actually publish on.
    # Pre-resolution it falls back to the accepted candidate purely so a collision
    # can still defer.
    if event is not None and resolved_occurrence is not None:
        occurrence_date = _parse_occurrence_date(_resolved_value(resolved_occurrence))
    else:
        occurrence_date = _parse_occurrence_date(
            _candidate_value(claims["candidate_occurrence_date"])
        )

    # Dedup before minting a competing recorded event: a date that already
    # publishes a different recorded event defers to human merge review.
    collision = published_recorded_event_on(session, occurrence_date)
    if collision is not None and not _manifest_is_wikidata_event(
        session, manifest=collision, qid=qid
    ):
        # A human may have already ruled that these are two different events that
        # happen to share a date. That decision -- pair-specific, current, and
        # theirs -- is the only thing that lets a second recorded event publish.
        bypass, blocking = _collision_adjudication(
            session, event=event, manifest=collision
        )
        if not bypass:
            if blocking is not None:
                return WikidataPublishOutcome(
                    status="blocked_by_adjudication",
                    occurrence_date=occurrence_date,
                    colliding_manifest_id=collision.id,
                    adjudication_id=blocking.id,
                )
            task = _ensure_merge_review_task(
                session,
                identity_claim=claims["candidate_event_identity"],
                qid=qid,
                occurrence_date=occurrence_date,
                colliding_manifest_id=collision.id,
                colliding_events=events_behind_manifest(
                    session, manifest=collision
                ),
            )
            return WikidataPublishOutcome(
                status="deferred_to_merge_review",
                occurrence_date=occurrence_date,
                colliding_manifest_id=collision.id,
                merge_review_task_id=task.id,
            )

    # Publication requires the resolved event (G2a).
    if event is None or identity_resolved is None or resolved_occurrence is None:
        raise ValueError(
            "The Wikidata candidate must be resolved into an event before publication."
        )
    methodology = _wikidata_methodology(session)
    profile_type = profile_type_for_date(occurrence_date)
    if profile_type is None:
        raise ValueError(
            "Wikidata occurrence date is outside the public archive band."
        )
    # The recorded event displays its temporal precision, assignment, and date role
    # from the resolved EventTime, as the recorded-event contract requires
    # (docs/PRODUCT_CONTRACT.md).
    event_time = session.scalar(
        select(EventTime).where(
            EventTime.event_id == event.id, EventTime.is_primary.is_(True)
        )
    )
    if event_time is None:
        raise ValueError("The resolved event has no primary occurrence time.")

    # What to publish, and in what order, comes from the human editorial ranking
    # (P2) -- not the source predicate order. An unranked candidate yields nothing
    # and is refused: the pass consumes the ranking stage, it never invents it.
    ranked = _ranked_recorded_predicates(
        session, qid=qid, profile_date=occurrence_date
    )
    if not ranked:
        raise ValueError(
            "Publication requires a human editorial selection for the recorded event."
        )
    # A recorded event must display its occurrence's temporal qualification
    # (docs/PRODUCT_CONTRACT.md): the occurrence root is what carries precision,
    # assignment, and date role, so a selection that omits it cannot publish.
    if "candidate_occurrence_date" not in {predicate for predicate, _ in ranked}:
        raise ValueError(
            "Publishing a recorded event requires its occurrence selection, which "
            "carries the temporal precision, assignment, and date role."
        )
    statements: list[dict[str, Any]] = []
    own_roots: list[UUID] = []
    roots_by_release: dict[UUID, set[UUID]] = {}
    for _index, (predicate, resolved) in enumerate(ranked):
        lineage_claim, lineage_release = _resolution_lineage(
            session, resolved=resolved
        )
        # Published provenance discloses the claim's data state (reported vs
        # estimated/modeled), as the recorded-event contract requires
        # (docs/PRODUCT_CONTRACT.md); the web reads it from details.data_status.
        details: dict[str, Any] = {
            **(
                dict(resolved.resolved_value)
                if isinstance(resolved.resolved_value, dict)
                else {}
            ),
            "data_status": lineage_claim.data_status.value,
        }
        if predicate == "candidate_occurrence_date":
            details = {
                **details,
                "temporal_precision": event_time.temporal_precision.value,
                "temporal_assignment": event_time.temporal_assignment.value,
                "date_role": event_time.date_role.value,
            }
        statements.append(
            {
                "statement_id": predicate.replace("candidate_", "wikidata-").replace(
                    "_", "-"
                ),
                "statement": _recorded_statement_text(
                    predicate, value=_resolved_value(resolved)
                ),
                "details": details,
                "provenance_note": (
                    "Wikidata CC0 structured data; single reviewed candidate."
                ),
                "provenance": _wikidata_statement_provenance(
                    claim=lineage_claim,
                    resolved=resolved,
                    release=lineage_release,
                    source=source,
                    methodology=methodology,
                ),
            }
        )
        own_roots.append(resolved.id)
        roots_by_release.setdefault(lineage_release.id, set()).add(resolved.id)

    # Publication consumes the human editorial-ranking stage. Each selected root is
    # gated against its *own* source release -- licensing, pipeline, quality, and
    # editorial -- so a root resolved from a newer release cannot bypass that
    # release's gates (docs/PRODUCT_CONTRACT.md source-release gate).
    for release_id, roots in roots_by_release.items():
        assert_release_publication_eligible(
            session,
            source_release_id=release_id,
            profile_date=occurrence_date,
            resolved_root_ids_by_section={"recorded_on_this_date": roots},
        )

    previous_manifest = session.scalar(
        select(PublicationManifest)
        .where(
            PublicationManifest.profile_date == occurrence_date,
            PublicationManifest.profile_type == profile_type,
            PublicationManifest.status == PublicationStatus.PUBLISHED,
        )
        .order_by(PublicationManifest.version.desc())
    )
    previous_profile = (
        session.scalar(
            select(DayProfile).where(
                DayProfile.publication_manifest_id == previous_manifest.id
            )
        )
        if previous_manifest is not None
        else None
    )

    # Featured means emphasized first, not retained alone. Every event this date
    # has already admitted stays published; the featured choice decides which one
    # leads. Dropping the others would not only lose them from the page -- it
    # would lose their identities from the manifest, and the collision guard
    # checks a later candidate against exactly that set.
    admitted_ids: list[UUID] = [event.id]
    retained_statements: list[dict[str, Any]] = []
    retained_roots: list[UUID] = []
    if previous_manifest is not None:
        retained_statements, retained_roots = _retained_recorded_statements(
            session,
            store=store,
            manifest=previous_manifest,
            profile_date=occurrence_date,
            rebuilt_key_prefix=f"wikidata:{qid}:",
        )
        for other_id in sorted(
            events_behind_manifest(session, manifest=previous_manifest), key=str
        ):
            if other_id != event.id:
                admitted_ids.append(other_id)

    identity_roots: dict[UUID, UUID] = {}
    for event_id in admitted_ids:
        admitted = session.get(Event, event_id)
        if admitted is None:
            raise ValueError(
                f"Event {event_id} was published on {occurrence_date.isoformat()} "
                "but no longer exists."
            )
        identity_roots[event_id] = admitted.resolved_claim_id

    # A date holding several events needs a human's choice of headline. The
    # deterministic default is a later slice; until then this fails closed
    # rather than lead with whichever event a query happened to order first.
    featured_root = resolve_featured_event(
        session,
        profile_date=occurrence_date,
        candidate_root_ids=[identity_roots[event_id] for event_id in admitted_ids],
    )
    featured_event_id = next(
        event_id
        for event_id, root in identity_roots.items()
        if root == featured_root
    )
    featured_selection = (
        current_featured_selection(
            session, profile_date=occurrence_date, root_id=featured_root
        )
        if featured_root is not None and len(admitted_ids) > 1
        else None
    )

    if featured_event_id == event.id:
        ordered_statements = statements + retained_statements
        ordered_roots = own_roots + retained_roots
    else:
        ordered_statements = retained_statements + statements
        ordered_roots = retained_roots + own_roots
    evidence = [
        PublicationStatementEvidenceInput(
            statement_path=f"/sections/recorded_on_this_date/{index}",
            resolved_claim_id=root,
        )
        for index, root in enumerate(ordered_roots)
    ]

    source_attribution = {
        "name": source.name,
        "publisher": source.publisher,
        "url": f"https://www.wikidata.org/wiki/{qid}",
    }
    if previous_manifest is None:
        payload = {
            "schema_version": "1",
            "date": occurrence_date.isoformat(),
            "profile_type": profile_type.value,
            "sections": {"recorded_on_this_date": ordered_statements},
            "section_states": {"recorded_on_this_date": {"status": "available"}},
            "source_attribution": source_attribution,
        }
    else:
        # Enrich the existing profile rather than replace it: carry every prior
        # section and its evidence forward and add the recorded event, so
        # publishing never drops the annual context a context profile holds (P1).
        base = store.read(previous_manifest.storage_uri, previous_manifest.content_hash)
        base_sections = base.get("sections")
        base_states = base.get("section_states")
        payload = {
            "schema_version": "1",
            "date": occurrence_date.isoformat(),
            "profile_type": profile_type.value,
            "sections": {
                **(base_sections if isinstance(base_sections, dict) else {}),
                "recorded_on_this_date": ordered_statements,
            },
            "section_states": {
                **(base_states if isinstance(base_states, dict) else {}),
                "recorded_on_this_date": {"status": "available"},
            },
            "source_attribution": source_attribution,
        }
        if isinstance(base.get("quality"), dict):
            payload["quality"] = base["quality"]
        evidence = _carried_forward_evidence(
            session, manifest_id=previous_manifest.id
        ) + evidence

    profile = publish_day_profile(
        session,
        store=store,
        profile_date=occurrence_date,
        profile_type=profile_type,
        payload=payload,
        statement_evidence=evidence,
        methodology_id=methodology.id,
        supersedes_manifest_id=(
            previous_manifest.id if previous_manifest is not None else None
        ),
        supersedes_day_profile_id=(
            previous_profile.id if previous_profile is not None else None
        ),
        editorial_revision=(
            previous_manifest.editorial_revision + 1
            if previous_manifest is not None
            else 1
        ),
        manifest_metadata={"wikidata_entity_id": qid},
        force_new_version=force_new_version,
    )
    # The version remembers which events it admitted and which it led with, so
    # the admitted set never has to be guessed back out of surviving statements.
    published_manifest = session.get(
        PublicationManifest, profile.publication_manifest_id
    )
    if published_manifest is None:  # pragma: no cover - just published
        raise ValueError("The published manifest could not be read back.")
    record_published_events(
        session,
        manifest=published_manifest,
        event_ids=admitted_ids,
        featured_event_id=featured_event_id,
        featured_selection_id=None if featured_selection is None else featured_selection.id,
    )
    return WikidataPublishOutcome(
        status="published",
        occurrence_date=occurrence_date,
        manifest_id=profile.publication_manifest_id,
        day_profile_id=profile.id,
    )
