from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_session
from app.models import (
    PUBLIC_DATE_MAX,
    PUBLIC_DATE_MIN,
    DayProfile,
    Methodology,
    ProfileType,
    PublicationManifest,
    PublicationStatus,
    Source,
    profile_type_for_date,
)
from app.services import LocalFilesystemPublishedProfileStore

settings = get_settings()
app = FastAPI(title="Day Perspective API", version=settings.service_version)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_origin],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["Content-Type"],
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
    return PublishedProfileResponse(
        status="published",
        date=profile_date,
        profile_type=profile_type,
        manifest_id=str(manifest.id),
        content_hash=manifest.content_hash,
        profile=profile_payload,
    )
