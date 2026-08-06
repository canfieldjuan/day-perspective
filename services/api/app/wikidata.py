from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
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
    FeaturedEventUnresolved,
    IdentityAdjudicationDecision,
    LicenseInput,
    assert_release_publication_eligible,
    evaluate_featured_event,
    event_group_key,
    events_behind_manifest,
    events_by_source_release,
    is_human_reviewer,
    latest_identity_adjudication,
    record_identity_adjudication,
    register_release_license,
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
    same question again with a fresh review task. ``featured_event_required``
    means a human ``distinct_event`` decision (D042) admits this candidate
    alongside another already-published event, but nobody has yet chosen which of
    the date's events is featured (D043); the pass publishes nothing rather than
    pick a headline itself (``colliding_manifest_id`` names the manifest that
    could not be safely extended).
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

    Excludes the event itself from the manifest's events before checking (G3b-1):
    once a manifest legitimately carries this event alongside another, this same
    check runs again on every later publish attempt to that manifest, and a
    self-pair would otherwise read as an unresolved adjudication against
    ourselves.
    """
    if event is None:
        return False, None
    others = events_behind_manifest(session, manifest=manifest) - {event.id}
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


def _collision_outcome_if_blocked(
    session: Session,
    *,
    event: Event | None,
    manifest: PublicationManifest,
    identity_claim: Claim,
    qid: str,
    occurrence_date: date,
) -> WikidataPublishOutcome | None:
    """``None`` to proceed past this manifest's collision; else the outcome to
    return instead.

    Shared by a brand-new candidate's collision check and the revalidation of
    events already carried onto a manifest that also contains our own event
    (G3b-1): a ``distinct_event`` decision can be superseded later by
    ``merge``/``supersede``/``deferred``, and that later decision must reach the
    same durable-adjudication rules the original guard applied, not be skipped
    merely because the manifest already includes this event.
    """
    bypass, blocking = _collision_adjudication(session, event=event, manifest=manifest)
    if bypass:
        return None
    if blocking is not None:
        return WikidataPublishOutcome(
            status="blocked_by_adjudication",
            occurrence_date=occurrence_date,
            colliding_manifest_id=manifest.id,
            adjudication_id=blocking.id,
        )
    task = _ensure_merge_review_task(
        session,
        identity_claim=identity_claim,
        qid=qid,
        occurrence_date=occurrence_date,
        colliding_manifest_id=manifest.id,
        colliding_events=events_behind_manifest(session, manifest=manifest),
    )
    return WikidataPublishOutcome(
        status="deferred_to_merge_review",
        occurrence_date=occurrence_date,
        colliding_manifest_id=manifest.id,
        merge_review_task_id=task.id,
    )


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


def _surviving_recorded_statements(
    session: Session,
    *,
    store: PublishedProfileStore,
    manifest: PublicationManifest,
    profile_date: date,
    exclude_root_ids: set[UUID],
) -> tuple[list[dict[str, Any]], list[PublicationStatementEvidenceInput]]:
    """A prior manifest's *other* recorded-event statements that are still selected.

    Rebuilt from current governance, not copied blindly (D042/D043): a
    predicate withdrawn since that manifest published must not survive into the
    successor merely because it appeared in the old artifact. The statement
    content itself is read back from the prior artifact -- the only place this
    module has a foreign source's own rendering of its own predicates -- but
    *which* predicates survive is decided fresh, from the current
    ``recorded_on_this_date`` editorial selections for this date, so a root a
    human has since rejected drops out even though the old artifact still
    names it.

    Deliberately a flat list, not grouped by event: only two of an event's
    published predicates (occurrence and, when present, coordinates) carry an
    ``Event``/``EventTime``/``EventLocation`` link at all -- the rest (name,
    magnitude, depth, type, ...) have no column tying them back to an event, by
    design (D044's alternatives): that mapping is source-specific rendering
    knowledge this module deliberately does not carry for a foreign source.
    Grouping by event from this data would silently drop every unlinked
    predicate for a carried-forward event, which is a worse defect than the
    ordering limitation this function accepts (see the caller for the
    ordering this implies when a date carries more than two admitted events).

    ``exclude_root_ids`` are the current candidate's own roots -- already
    freshly rendered by the caller -- so its predicates are never duplicated
    between its own fresh statements and this function's carried-forward ones.
    """
    prior_payload = store.read(manifest.storage_uri, manifest.content_hash)
    prior_sections = prior_payload.get("sections")
    prior_statements = (
        prior_sections.get("recorded_on_this_date")
        if isinstance(prior_sections, dict)
        else None
    )
    if not isinstance(prior_statements, list):
        return [], []
    evidence_by_path = {
        row.statement_path: row
        for row in session.scalars(
            select(PublicationStatementEvidence).where(
                PublicationStatementEvidence.publication_manifest_id == manifest.id,
                PublicationStatementEvidence.statement_path.startswith(
                    "/sections/recorded_on_this_date/", autoescape=True
                ),
            )
        )
    }
    latest_selected: dict[UUID, EditorialSelection] = {}
    for selection in session.scalars(
        select(EditorialSelection)
        .where(
            EditorialSelection.profile_date == profile_date,
            EditorialSelection.section_key == "recorded_on_this_date",
        )
        .order_by(EditorialSelection.decision_version.desc())
    ):
        if selection.resolved_claim_id is not None:
            latest_selected.setdefault(selection.resolved_claim_id, selection)

    surviving_statements: list[dict[str, Any]] = []
    surviving_evidence: list[PublicationStatementEvidenceInput] = []
    for index, statement in enumerate(prior_statements):
        row = evidence_by_path.get(f"/sections/recorded_on_this_date/{index}")
        if row is None or row.resolved_claim_id is None:
            continue
        if row.resolved_claim_id in exclude_root_ids:
            continue
        decision = latest_selected.get(row.resolved_claim_id)
        if decision is None or decision.status != EditorialSelectionStatus.SELECTED.value:
            continue
        surviving_statements.append(statement)
        surviving_evidence.append(
            PublicationStatementEvidenceInput(
                statement_path=row.statement_path,
                resolved_claim_id=row.resolved_claim_id,
            )
        )
    return surviving_statements, surviving_evidence


def _event_has_surviving_evidence(
    session: Session, *, event_id: UUID | None, surviving_root_ids: set[UUID]
) -> bool:
    """Whether any surviving statement's root is actually this event's own.

    The featured pick (the ``featured_event`` section) and per-predicate
    rejection (the ``recorded_on_this_date`` section) are independent
    governance decisions on independent roots, so a featured prior event can
    have every one of its own predicates separately rejected without the
    featured selection itself ever being touched. Checked only via the two
    roots that do link back to an event -- occurrence and, when present,
    location -- the same two `events_behind_manifest` reads; this function
    answers one existence question, not the full per-statement attribution
    `_surviving_recorded_statements` deliberately does not attempt.
    """
    if event_id is None or not surviving_root_ids:
        return False
    return bool(
        session.scalar(
            select(func.count())
            .select_from(EventTime)
            .where(
                EventTime.event_id == event_id,
                EventTime.provenance_resolved_claim_id.in_(surviving_root_ids),
            )
        )
    ) or bool(
        session.scalar(
            select(func.count())
            .select_from(EventLocation)
            .where(
                EventLocation.event_id == event_id,
                EventLocation.provenance_resolved_claim_id.in_(surviving_root_ids),
            )
        )
    )


def _grouped_recorded_statements(
    session: Session,
    *,
    statements: list[dict[str, Any]],
    evidence: list[PublicationStatementEvidenceInput],
    featured_event_id: UUID,
    event_ids: Sequence[UUID],
) -> list[dict[str, Any]]:
    """Stamp each recorded statement with the event it describes.

    Grouping stated rather than positional. A renderer that inferred groups from
    array order would be guessing where one event ends, and the guess breaks as
    soon as a date holds three events or one of them contributes a single
    statement.

    Attribution runs through source-release lineage, which every publisher has,
    rather than identity or occurrence provenance, which only some statements
    root on. A statement whose release cannot be tied to exactly one event is
    left ungrouped: rendering it outside a group is a visible gap, while filing
    it under the wrong event tells a reader that a different thing happened.

    The featured event is ``event_order`` 0 and leads; ``predicate_order`` runs
    from 0 within each group, so a group stays ordered when rendered alone.
    """
    owners = events_by_source_release(session, event_ids=event_ids)
    titles: dict[UUID, tuple[str, str]] = {}
    for event_id in event_ids:
        event = session.get(Event, event_id)
        identity = (
            None if event is None else session.get(ResolvedClaim, event.resolved_claim_id)
        )
        if event is None or identity is None:
            continue
        titles[event_id] = (
            event_group_key(identity.canonical_key),
            event.canonical_title,
        )

    attributed: list[UUID | None] = []
    for item in evidence:
        owner: UUID | None = None
        if item.resolved_claim_id is not None:
            resolved = session.get(ResolvedClaim, item.resolved_claim_id)
            if resolved is not None:
                claim, _release = _resolution_lineage(session, resolved=resolved)
                owner = owners.get(claim.source_release_id)
        attributed.append(owner)

    ordering = [featured_event_id] + [
        event_id for event_id in event_ids if event_id != featured_event_id
    ]
    event_order = {event_id: index for index, event_id in enumerate(ordering)}
    predicate_counter: dict[UUID, int] = {}
    grouped: list[dict[str, Any]] = []
    for statement, owner in zip(statements, attributed, strict=True):
        if owner is None or owner not in titles:
            grouped.append(statement)
            continue
        key, title = titles[owner]
        position = predicate_counter.get(owner, 0)
        predicate_counter[owner] = position + 1
        grouped.append(
            {
                **statement,
                "event_group": {
                    "event_group_key": key,
                    "event_title": title,
                    "featured": owner == featured_event_id,
                    "event_order": event_order.get(owner, len(ordering)),
                    "predicate_order": position,
                },
            }
        )
    return grouped


def _source_attributions(
    session: Session, *, event_ids: Sequence[UUID]
) -> list[dict[str, str]]:
    """Every source whose evidence supports this date's recorded section.

    A singular attribution names whichever publisher wrote last, which on a
    multi-source date is not a summary but a false claim -- and false in the
    direction that flatters the most recent contributor. Statement-level
    provenance stays authoritative; this is the page-level summary of it.
    """
    owners = events_by_source_release(session, event_ids=event_ids)
    attributions: list[dict[str, str]] = []
    seen: set[UUID] = set()
    for release_id in owners:
        if release_id in seen:
            continue
        seen.add(release_id)
        release = session.get(SourceRelease, release_id)
        if release is None:
            continue
        origin = session.get(Source, release.source_id)
        if origin is None:
            continue
        attributions.append(
            {
                "name": origin.name,
                "publisher": origin.publisher or "",
                "url": origin.canonical_url or "",
            }
        )
    attributions.sort(key=lambda entry: entry["name"])
    return attributions


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
        outcome = _collision_outcome_if_blocked(
            session,
            event=event,
            manifest=collision,
            identity_claim=claims["candidate_event_identity"],
            qid=qid,
            occurrence_date=occurrence_date,
        )
        if outcome is not None:
            return outcome

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
    evidence: list[PublicationStatementEvidenceInput] = []
    roots_by_release: dict[UUID, set[UUID]] = {}
    for index, (predicate, resolved) in enumerate(ranked):
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
        evidence.append(
            PublicationStatementEvidenceInput(
                statement_path=f"/sections/recorded_on_this_date/{index}",
                resolved_claim_id=resolved.id,
            )
        )
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

    # Other canonical events the prior manifest already admits, besides this
    # candidate's own. Non-empty exactly when a human has adjudicated a genuine
    # identity collision distinct (D042): the successor must retain every
    # admitted event, never replace one with another, and must carry exactly
    # one featured choice among them (D043). No standing rule exists yet
    # (G3b-2), so an unresolved choice fails closed rather than publish an
    # arbitrary headline.
    other_events = (
        events_behind_manifest(session, manifest=previous_manifest) - {event.id}
        if previous_manifest is not None
        else set()
    )
    recorded_statements = statements
    recorded_evidence = evidence
    featured_metadata: dict[str, Any] = {}
    if other_events:
        # other_events is only ever non-empty when previous_manifest is not
        # None (see its definition immediately above).
        assert previous_manifest is not None
        # Re-check the collision guard even though this manifest already
        # carries our own event: the earlier check above is skipped whenever
        # _manifest_is_wikidata_event is true, but a distinct_event decision
        # that once admitted these other events can be superseded later by a
        # human recording merge/supersede/deferred for the same pair. Without
        # this, a stale decision would go unenforced on every later republish
        # that merely carries the other event forward (G3b-1 round 1).
        outcome = _collision_outcome_if_blocked(
            session,
            event=event,
            manifest=previous_manifest,
            identity_claim=claims["candidate_event_identity"],
            qid=qid,
            occurrence_date=occurrence_date,
        )
        if outcome is not None:
            return outcome
        candidate_roots = [identity_resolved.id]
        for other_event_id in sorted(other_events, key=str):
            other_event = session.get(Event, other_event_id)
            if other_event is not None:
                candidate_roots.append(other_event.resolved_claim_id)
        surviving_statements, surviving_evidence = _surviving_recorded_statements(
            session,
            store=store,
            manifest=previous_manifest,
            profile_date=occurrence_date,
            exclude_root_ids={
                item.resolved_claim_id
                for item in evidence
                if item.resolved_claim_id is not None
            },
        )
        surviving_root_ids = {
            item.resolved_claim_id
            for item in surviving_evidence
            if item.resolved_claim_id is not None
        }
        if any(
            not _event_has_surviving_evidence(
                session, event_id=other_event_id, surviving_root_ids=surviving_root_ids
            )
            for other_event_id in other_events
        ):
            # Featuring and per-predicate rejection are independent governance
            # decisions: a human can reject every one of an admitted event's
            # own recorded predicates -- whether or not that event is the
            # featured one -- without ever touching its adjudication or
            # featured-event standing. If nothing of an admitted event
            # survives, carrying it forward silently drops its evidence from
            # the successor manifest, which makes it invisible to
            # `events_behind_manifest` on every later publish attempt -- a
            # future candidate would then never be checked against an event
            # the date had genuinely admitted. Fail closed instead, for every
            # `other_events` member, not only whichever one is featured.
            return WikidataPublishOutcome(
                status="featured_event_required",
                occurrence_date=occurrence_date,
                colliding_manifest_id=previous_manifest.id,
            )
        try:
            # Evaluates rather than merely resolves: where no person has chosen,
            # the standing rule supplies a deterministic default so a date with
            # two events is publishable without inventing a human decision. A
            # human choice, present or newly ineligible, is handled inside --
            # the rule never displaces one, and never silently succeeds one that
            # has stopped qualifying.
            evaluation = evaluate_featured_event(
                session,
                profile_date=occurrence_date,
                candidate_root_ids=candidate_roots,
            )
        except FeaturedEventUnresolved:
            return WikidataPublishOutcome(
                status="featured_event_required",
                occurrence_date=occurrence_date,
                colliding_manifest_id=previous_manifest.id,
            )
        # At least two candidate roots are always supplied here, so the
        # evaluation never returns None (that is only for fewer than two).
        assert evaluation is not None
        featured_root = evaluation.winning_root_id
        # Every published version records the candidate set *it* evaluated, even
        # when the headline did not move: editorial history says when a decision
        # changed, and a manifest has to say what this version considered.
        featured_metadata = evaluation.as_manifest_metadata()
        # Only a two-way split: featured-vs-not. A date with more than two
        # admitted events, where the featured one is neither this candidate nor
        # first in the prior artifact's order, is not reachable through any
        # real publisher in this codebase today (only one real Wikidata entity
        # exists, alongside USGS's single golden event) and is deliberately not
        # handled precisely here -- see the module-level note on
        # `_surviving_recorded_statements` for why a per-event grouping fix is
        # deferred rather than approximated.
        if featured_root == identity_resolved.id:
            recorded_statements = [*statements, *surviving_statements]
            recorded_evidence = [*evidence, *surviving_evidence]
        else:
            recorded_statements = [*surviving_statements, *statements]
            recorded_evidence = [*surviving_evidence, *evidence]
        admitted_event_ids = [event.id, *sorted(other_events, key=str)]
        recorded_statements = _grouped_recorded_statements(
            session,
            statements=recorded_statements,
            evidence=recorded_evidence,
            featured_event_id=(
                event.id
                if featured_root == identity_resolved.id
                else next(
                    other_id
                    for other_id in other_events
                    if (other := session.get(Event, other_id)) is not None
                    and other.resolved_claim_id == featured_root
                )
            ),
            event_ids=admitted_event_ids,
        )
        recorded_evidence = [
            PublicationStatementEvidenceInput(
                statement_path=f"/sections/recorded_on_this_date/{index}",
                resolved_claim_id=item.resolved_claim_id,
                derived_value_id=item.derived_value_id,
            )
            for index, item in enumerate(recorded_evidence)
        ]

    if not other_events:
        # One event is still an event. Publishing the same shape means a renderer
        # never has to branch on whether grouping is present.
        admitted_event_ids = [event.id]
        recorded_statements = _grouped_recorded_statements(
            session,
            statements=recorded_statements,
            evidence=recorded_evidence,
            featured_event_id=event.id,
            event_ids=admitted_event_ids,
        )

    # Every source whose evidence supports the recorded section, not whichever
    # publisher wrote last.
    source_attributions = _source_attributions(
        session, event_ids=admitted_event_ids
    )
    if previous_manifest is None:
        payload = {
            "schema_version": "1",
            "date": occurrence_date.isoformat(),
            "profile_type": profile_type.value,
            "sections": {"recorded_on_this_date": recorded_statements},
            "section_states": {"recorded_on_this_date": {"status": "available"}},
            "source_attributions": source_attributions,
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
                "recorded_on_this_date": recorded_statements,
            },
            "section_states": {
                **(base_states if isinstance(base_states, dict) else {}),
                "recorded_on_this_date": {"status": "available"},
            },
            "source_attributions": source_attributions,
        }
        if isinstance(base.get("quality"), dict):
            payload["quality"] = base["quality"]
        recorded_evidence = _carried_forward_evidence(
            session, manifest_id=previous_manifest.id
        ) + recorded_evidence

    # publish_day_profile's idempotency decides purely on the rendered payload's
    # content hash. A human can reaffirm or replace the current featured choice
    # for the *same* root -- a new EditorialSelection version with the same
    # outcome -- which changes nothing about the rendered section or its order.
    # Without this, that republish would be treated as a no-op and the manifest
    # would keep pointing at the stale selection row/version even though this
    # publish resolved against a newer one.
    # Every field of the evaluation, not just the selection it resolved to. The
    # candidate set can change while the headline does not -- another publisher
    # adding a losing event -- and the rendered section is then byte-identical,
    # so content-hash idempotency would treat the republish as a no-op and leave
    # the current manifest claiming a candidate set that is no longer the one
    # evaluated. Comparing the whole binding keeps publication provenance
    # honest rather than merely stable.
    metadata_binding_changed = (
        bool(featured_metadata)
        and previous_manifest is not None
        and any(
            previous_manifest.metadata_json.get(field) != value
            for field, value in featured_metadata.items()
        )
    )

    profile = publish_day_profile(
        session,
        store=store,
        profile_date=occurrence_date,
        profile_type=profile_type,
        payload=payload,
        statement_evidence=recorded_evidence,
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
        manifest_metadata={"wikidata_entity_id": qid, **featured_metadata},
        force_new_version=force_new_version or metadata_binding_changed,
    )
    return WikidataPublishOutcome(
        status="published",
        occurrence_date=occurrence_date,
        manifest_id=profile.publication_manifest_id,
        day_profile_id=profile.id,
    )
