from __future__ import annotations

import datetime as dtmod
from datetime import date
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.coverage import coverage_for_date, coverage_summary
from app.database import get_session
from app.governance import ReviewDecisionValue, record_claim_review
from app.models import (
    PUBLIC_DATE_MAX,
    PUBLIC_DATE_MIN,
    Claim,
    DayProfile,
    Methodology,
    ProfileType,
    PublicationManifest,
    PublicationStatus,
    ResolvedClaimEvidence,
    ReviewTask,
    Source,
    SourceRelease,
    profile_type_for_date,
)
from app.services import LocalFilesystemPublishedProfileStore, record_correction
from app.usgs import (
    USGS_SOURCE_SLUG,
    accept_and_resolve_release,
    publish_golden_profile,
)

settings = get_settings()
NonBlankRationale = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1)
]
app = FastAPI(title="Day Perspective API", version=settings.service_version)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_origin],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Development-Review-Token"],
)


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthResponse(APIModel):
    status: Literal["ok"]


class SystemStatusResponse(APIModel):
    service: str
    status: Literal["ready"]
    data_mode: Literal["offline_import_and_published_profiles_only"]
    public_date_min: date
    public_date_max: date
    profile_storage: Literal["local_filesystem_development_implementation"]
    live_third_party_requests: Literal[False]


class MethodologyResponse(APIModel):
    id: str
    slug: str
    version: str
    name: str
    description: str
    code_version: str
    legal_review_status: str


class MethodologiesResponse(APIModel):
    methodologies: list[MethodologyResponse]


class SourceResponse(APIModel):
    id: str
    slug: str
    name: str
    publisher: str | None
    canonical_url: str | None
    legal_review_status: str


class SourcesResponse(APIModel):
    sources: list[SourceResponse]


class CoverageDateResponse(APIModel):
    status: Literal["coverage"]
    date: dtmod.date
    profile_type: str
    publication_tier: str
    has_recorded_event: bool
    sections: dict[str, int]
    quality_floor: str | None
    review_status: str
    index_version: int
    nearest_enriched_before: dtmod.date | None
    nearest_enriched_after: dtmod.date | None
    nearest_recorded_event_before: dtmod.date | None
    nearest_recorded_event_after: dtmod.date | None


class CoverageNotIndexedResponse(APIModel):
    status: Literal["coverage_not_indexed"]
    date: dtmod.date
    detail: str


class CoverageSummaryResponse(APIModel):
    status: Literal["coverage_summary"]
    total_published: int
    by_tier: dict[str, int]
    with_recorded_event: int
    earliest: dtmod.date | None
    latest: dtmod.date | None
    index_version: int
    supported_range: dict[str, str]


class ProfileNotPublishedResponse(APIModel):
    status: Literal["profile_not_published"]
    date: date
    profile_type: ProfileType
    detail: str


class DateOutOfSupportedRangeResponse(APIModel):
    status: Literal["date_out_of_supported_range"]
    minimum: date
    maximum: date


class ProfileStorageUnavailableResponse(APIModel):
    status: Literal["profile_storage_unavailable"]
    detail: str


class PublishedProfileResponse(APIModel):
    status: Literal["published"]
    date: date
    profile_type: ProfileType
    manifest_id: str
    content_hash: str
    profile: dict[str, Any]


def profile_store() -> LocalFilesystemPublishedProfileStore:
    return LocalFilesystemPublishedProfileStore(settings.published_profile_root)


def development_review_guard(
    token: Annotated[str | None, Header(alias="X-Development-Review-Token")] = None,
) -> None:
    if token != settings.development_review_token:
        raise HTTPException(
            status_code=403,
            detail=(
                "Development-only review guard rejected the request. "
                "This mechanism is not production authentication."
            ),
        )


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/api/v1/system/status", response_model=SystemStatusResponse)
def system_status() -> SystemStatusResponse:
    return SystemStatusResponse(
        service=settings.service_name,
        status="ready",
        data_mode="offline_import_and_published_profiles_only",
        public_date_min=PUBLIC_DATE_MIN,
        public_date_max=PUBLIC_DATE_MAX,
        profile_storage="local_filesystem_development_implementation",
        live_third_party_requests=False,
    )


@app.get("/api/v1/methodologies", response_model=MethodologiesResponse)
def methodologies(session: Annotated[Session, Depends(get_session)]) -> MethodologiesResponse:
    rows = session.scalars(select(Methodology).order_by(Methodology.slug, Methodology.version)).all()
    return MethodologiesResponse(
        methodologies=[
            MethodologyResponse(
                id=str(row.id),
                slug=row.slug,
                version=row.version,
                name=row.name,
                description=row.description,
                code_version=row.code_version,
                legal_review_status=row.legal_review_status.value,
            )
            for row in rows
        ]
    )


@app.get("/api/v1/sources", response_model=SourcesResponse)
def sources(session: Annotated[Session, Depends(get_session)]) -> SourcesResponse:
    rows = session.scalars(select(Source).order_by(Source.slug)).all()
    return SourcesResponse(
        sources=[
            SourceResponse(
                id=str(row.id),
                slug=row.slug,
                name=row.name,
                publisher=row.publisher,
                canonical_url=row.canonical_url,
                legal_review_status=row.legal_review_status.value,
            )
            for row in rows
        ]
    )


@app.get("/api/v1/coverage", response_model=CoverageSummaryResponse)
def coverage_overview(
    session: Annotated[Session, Depends(get_session)],
) -> CoverageSummaryResponse:
    """How much of the archive is published, and how rich it is."""
    summary = coverage_summary(session)
    return CoverageSummaryResponse(
        status="coverage_summary",
        total_published=summary.total_published,
        by_tier=summary.by_tier,
        with_recorded_event=summary.with_recorded_event,
        earliest=summary.earliest,
        latest=summary.latest,
        index_version=summary.index_version,
        supported_range={
            "min": PUBLIC_DATE_MIN.isoformat(),
            "max": PUBLIC_DATE_MAX.isoformat(),
        },
    )


@app.get("/api/v1/coverage/{profile_date}", response_model=None)
def coverage_for_profile_date(
    profile_date: dtmod.date,
    session: Annotated[Session, Depends(get_session)],
) -> JSONResponse | CoverageDateResponse:
    """This date's richness, and the nearest dates worth travelling to."""
    if profile_type_for_date(profile_date) is None:
        out_of_range = DateOutOfSupportedRangeResponse(
            status="date_out_of_supported_range",
            minimum=PUBLIC_DATE_MIN,
            maximum=PUBLIC_DATE_MAX,
        )
        return JSONResponse(
            status_code=404, content=out_of_range.model_dump(mode="json")
        )
    record = coverage_for_date(session, profile_date)
    if record is None:
        not_indexed = CoverageNotIndexedResponse(
            status="coverage_not_indexed",
            date=profile_date,
            detail="No published profile is indexed for this date.",
        )
        return JSONResponse(
            status_code=404, content=not_indexed.model_dump(mode="json")
        )
    return CoverageDateResponse(
        status="coverage",
        date=record.profile_date,
        profile_type=record.profile_type.value,
        publication_tier=record.publication_tier.value,
        has_recorded_event=record.has_recorded_event,
        sections=record.sections,
        quality_floor=record.quality_floor,
        review_status=record.review_status,
        index_version=record.index_version,
        nearest_enriched_before=record.nearest_enriched_before,
        nearest_enriched_after=record.nearest_enriched_after,
        nearest_recorded_event_before=record.nearest_recorded_event_before,
        nearest_recorded_event_after=record.nearest_recorded_event_after,
    )


@app.get("/api/v1/day/{profile_date}", response_model=PublishedProfileResponse)
def day_profile(
    profile_date: date,
    session: Annotated[Session, Depends(get_session)],
) -> PublishedProfileResponse | JSONResponse:
    profile_type = profile_type_for_date(profile_date)
    if profile_type is None:
        out_of_range = DateOutOfSupportedRangeResponse(
            status="date_out_of_supported_range",
            minimum=PUBLIC_DATE_MIN,
            maximum=PUBLIC_DATE_MAX,
        )
        return JSONResponse(
            status_code=404,
            content=out_of_range.model_dump(mode="json"),
        )
    row = session.execute(
        select(DayProfile, PublicationManifest)
        .join(PublicationManifest, DayProfile.publication_manifest_id == PublicationManifest.id)
        .where(
            DayProfile.profile_date == profile_date,
            DayProfile.profile_type == profile_type,
            PublicationManifest.status == PublicationStatus.PUBLISHED,
        )
        .order_by(PublicationManifest.version.desc())
    ).first()
    if row is None:
        not_published = ProfileNotPublishedResponse(
            status="profile_not_published",
            date=profile_date,
            profile_type=profile_type,
            detail="No profile has been published for this date yet.",
        )
        return JSONResponse(
            status_code=404,
            content=not_published.model_dump(mode="json"),
        )
    _, manifest = row
    try:
        profile_payload = profile_store().read(manifest.storage_uri, manifest.content_hash)
    except (OSError, RuntimeError, ValueError):
        storage_unavailable = ProfileStorageUnavailableResponse(
            status="profile_storage_unavailable",
            detail="Published artifact integrity could not be verified.",
        )
        return JSONResponse(
            status_code=503,
            content=storage_unavailable.model_dump(mode="json"),
        )
    # The manifest is the authority for the publication tier: artifacts
    # published before the tier existed are byte-immutable and carry none,
    # and their backfilled tier would otherwise be invisible to the API and
    # interface while coverage queries reported it (D031). Verification above
    # still runs against the untouched artifact bytes.
    profile_payload = {
        **profile_payload,
        "publication_tier": manifest.publication_tier.value,
    }
    return PublishedProfileResponse(
        status="published",
        date=profile_date,
        profile_type=profile_type,
        manifest_id=str(manifest.id),
        content_hash=manifest.content_hash,
        profile=profile_payload,
    )


@app.get(
    "/api/v1/admin/claims",
    dependencies=[Depends(development_review_guard)],
)
def admin_claims(
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    rows = session.scalars(select(Claim).order_by(Claim.imported_at, Claim.claim_type)).all()
    return {
        "development_only": True,
        "claims": [
            {
                "claim_id": str(row.id),
                "source_release_id": str(row.source_release_id),
                "predicate": row.claim_type,
                "value": row.assertion_json,
                "status": row.assertion_status.value,
                "source_record_locator": row.source_record_locator,
            }
            for row in rows
        ],
    }


@app.get(
    "/api/v1/admin/releases",
    dependencies=[Depends(development_review_guard)],
)
def admin_releases(
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    releases = session.scalars(
        select(SourceRelease).order_by(SourceRelease.ingested_at)
    ).all()
    return {
        "development_only": True,
        "releases": [
            {
                "release_id": str(release.id),
                "release_label": release.release_label,
                "source_slug": (
                    source.slug
                    if (source := session.get(Source, release.source_id)) is not None
                    else None
                ),
                "resolution_supported": (
                    source is not None and source.slug == USGS_SOURCE_SLUG
                ),
                "source_url": release.source_url,
                "checksum": release.raw_checksum_sha256,
                "claim_statuses": sorted(
                    {
                        claim.assertion_status.value
                        for claim in session.scalars(
                            select(Claim).where(
                                Claim.source_release_id == release.id
                            )
                        )
                    }
                ),
            }
            for release in releases
        ],
    }


@app.get(
    "/api/v1/admin/conflicts",
    dependencies=[Depends(development_review_guard)],
)
def admin_conflicts(
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    rows = session.scalars(
        select(ResolvedClaimEvidence).where(ResolvedClaimEvidence.stance == "dissenting")
    ).all()
    return {
        "development_only": True,
        "dissenting_evidence": [
            {
                "resolved_claim_id": str(row.resolved_claim_id),
                "claim_id": str(row.claim_id),
                "note": row.note,
            }
            for row in rows
        ],
    }


@app.get(
    "/api/v1/admin/manifests",
    dependencies=[Depends(development_review_guard)],
)
def admin_manifests(
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    manifests = session.scalars(
        select(PublicationManifest).order_by(
            PublicationManifest.profile_date,
            PublicationManifest.version,
        )
    ).all()
    return {
        "development_only": True,
        "manifests": [
            {
                "manifest_id": str(manifest.id),
                "date": manifest.profile_date.isoformat(),
                "version": manifest.version,
                "status": manifest.status.value,
                "content_hash": manifest.content_hash,
                "supersedes_manifest_id": (
                    str(manifest.supersedes_manifest_id)
                    if manifest.supersedes_manifest_id
                    else None
                ),
            }
            for manifest in manifests
        ],
    }


class CorrectionRequest(APIModel):
    original_manifest_id: UUID
    replacement_manifest_id: UUID
    rationale: NonBlankRationale


@app.post(
    "/api/v1/admin/corrections",
    dependencies=[Depends(development_review_guard)],
)
def admin_record_correction(
    request: CorrectionRequest,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    try:
        correction = record_correction(
            session,
            original_manifest_id=request.original_manifest_id,
            replacement_manifest_id=request.replacement_manifest_id,
            rationale=request.rationale,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    session.commit()
    return {
        "correction_id": str(correction.id),
        "original_manifest_id": str(correction.original_manifest_id),
        "replacement_manifest_id": str(correction.replacement_manifest_id),
    }


@app.get(
    "/api/v1/admin/review-tasks",
    dependencies=[Depends(development_review_guard)],
)
def admin_review_tasks(
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    rows = session.scalars(select(ReviewTask).order_by(ReviewTask.created_at)).all()
    return {
        "development_only": True,
        "tasks": [
            {
                "task_id": str(row.id),
                "claim_id": str(row.claim_id) if row.claim_id else None,
                "status": row.status,
                "rationale": row.rationale,
            }
            for row in rows
        ],
    }


class ClaimDecisionRequest(APIModel):
    decision: Literal["accepted", "rejected"]
    rationale: NonBlankRationale


@app.post(
    "/api/v1/admin/claims/{claim_id}/decision",
    dependencies=[Depends(development_review_guard)],
)
def admin_claim_decision(
    claim_id: UUID,
    request: ClaimDecisionRequest,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    claim = session.get(Claim, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found.")
    try:
        record_claim_review(
            session,
            claim=claim,
            decision=ReviewDecisionValue(request.decision),
            rationale=request.rationale,
            reviewed_by="development-review-api",
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    session.commit()
    return {"claim_id": str(claim.id), "status": claim.assertion_status.value}


@app.post(
    "/api/v1/admin/releases/{release_id}/resolve",
    dependencies=[Depends(development_review_guard)],
)
def admin_resolve_release(
    release_id: str,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    try:
        resolved = accept_and_resolve_release(
            session,
            UUID(release_id),
            review_candidates=False,
        )
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    session.commit()
    return {
        "resolved": [
            {"predicate": key, "canonical_key": row.canonical_key, "version": row.version}
            for key, row in sorted(resolved.items())
        ]
    }


@app.post(
    "/api/v1/admin/day/1964-03-27/publish",
    dependencies=[Depends(development_review_guard)],
)
def admin_publish_golden(
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    try:
        profile = publish_golden_profile(session, store=profile_store())
    except (OSError, RuntimeError, ValueError) as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    session.commit()
    manifest = session.get(PublicationManifest, profile.publication_manifest_id)
    return {
        "day_profile_id": str(profile.id),
        "manifest_id": str(profile.publication_manifest_id),
        "version": manifest.version if manifest else None,
    }


@app.get(
    "/api/v1/admin/manifests/{manifest_id}",
    dependencies=[Depends(development_review_guard)],
)
def admin_manifest(
    manifest_id: str,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    try:
        manifest = session.get(PublicationManifest, UUID(manifest_id))
    except (TypeError, ValueError):
        manifest = None
    if manifest is None:
        raise HTTPException(status_code=404, detail="Manifest not found.")
    return {
        "manifest_id": str(manifest.id),
        "date": manifest.profile_date.isoformat(),
        "version": manifest.version,
        "editorial_revision": manifest.editorial_revision,
        "status": manifest.status.value,
        "content_hash": manifest.content_hash,
        "object_location": f"/{manifest.storage_uri}",
        "metadata": manifest.metadata_json,
    }
