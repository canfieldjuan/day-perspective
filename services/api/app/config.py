from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = Field(
        default="postgresql+psycopg://day_perspective:day_perspective@localhost:54329/day_perspective",
        validation_alias="DATABASE_URL",
    )
    published_profile_root: Path = Field(
        default=Path("../../.local/published-profiles"),
        validation_alias="PUBLISHED_PROFILE_ROOT",
    )
    raw_source_root: Path = Field(
        default=Path("../../.local/raw-sources"),
        validation_alias="RAW_SOURCE_ROOT",
    )
    development_review_token: str = Field(
        default="development-only-not-authentication",
        validation_alias="DEVELOPMENT_REVIEW_TOKEN",
    )
    service_name: str = "day-perspective-api"
    service_version: str = "0.1.0"
    web_origin: str = Field(default="http://localhost:3000", validation_alias="WEB_ORIGIN")
    allow_test_fixtures: bool = Field(
        default=False,
        validation_alias="DAY_PERSPECTIVE_ALLOW_TEST_FIXTURES",
    )
    # Pinned timezone-boundary-builder release (A3). The seed refuses any
    # download whose SHA256 differs, so the boundary data a day is derived under
    # is exactly this release (D013). Bump the tag, URL, and checksum together.
    timezone_boundary_dataset_version: str = Field(
        default="2026d",
        validation_alias="TIMEZONE_BOUNDARY_DATASET_VERSION",
    )
    timezone_boundary_url: str = Field(
        default=(
            "https://github.com/evansiroky/timezone-boundary-builder/"
            "releases/download/2026d/timezones.geojson.zip"
        ),
        validation_alias="TIMEZONE_BOUNDARY_URL",
    )
    timezone_boundary_sha256: str = Field(
        default="f72a40a0d00ef4464ad5f6dee85e0bf727e3fbe5ade360d8830f5a1db7d5f44b",
        validation_alias="TIMEZONE_BOUNDARY_SHA256",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
