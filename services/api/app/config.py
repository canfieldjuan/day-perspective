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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
