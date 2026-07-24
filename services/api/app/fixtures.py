from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import LegalReviewStatus, Methodology, Source, SourceRelease
from app.services import create_source_release


def _fixture_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "fixtures" / "test_only_seed.json"


def _raw_fixture_path(raw_storage_uri: str) -> Path:
    prefix = "fixtures://"
    if not raw_storage_uri.startswith(prefix):
        raise RuntimeError("Test fixture raw storage URI must use fixtures://.")
    fixture_root = _fixture_path().parent.resolve()
    raw_path = (fixture_root / raw_storage_uri.removeprefix(prefix)).resolve()
    if not raw_path.is_relative_to(fixture_root):
        raise RuntimeError("Test fixture raw storage URI escapes the fixture directory.")
    return raw_path


def seed_test_fixtures() -> None:
    if not get_settings().allow_test_fixtures:
        raise RuntimeError(
            "Refusing test fixtures without DAY_PERSPECTIVE_ALLOW_TEST_FIXTURES=1."
        )
    payload: dict[str, Any] = json.loads(_fixture_path().read_text(encoding="utf-8"))
    marker = "TEST_FIXTURE_ONLY_NOT_HISTORICAL_EVIDENCE"
    if payload.get("fixture_marker") != marker:
        raise RuntimeError("Fixture marker missing; refusing to seed.")
    release_data = payload["release"]
    raw_artifact = _raw_fixture_path(release_data["raw_storage_uri"])
    raw_checksum_sha256 = hashlib.sha256(raw_artifact.read_bytes()).hexdigest()
    if raw_checksum_sha256 != release_data["raw_checksum_sha256"]:
        raise RuntimeError("Test fixture raw artifact checksum does not match its declared source release.")
    with SessionLocal() as session:
        source_data = payload["source"]
        source = session.scalar(select(Source).where(Source.slug == source_data["slug"]))
        if source is None:
            source = Source(
                slug=source_data["slug"],
                name=source_data["name"],
                publisher=source_data["publisher"],
                canonical_url=source_data["canonical_url"],
                legal_review_status=LegalReviewStatus.NOT_REQUIRED,
            )
            session.add(source)
            session.flush()
        methodology_data = payload["methodology"]
        methodology = session.scalar(
            select(Methodology).where(
                Methodology.slug == methodology_data["slug"],
                Methodology.version == methodology_data["version"],
            )
        )
        if methodology is None:
            session.add(
                Methodology(
                    slug=methodology_data["slug"],
                    version=methodology_data["version"],
                    name=methodology_data["name"],
                    description=methodology_data["description"],
                    code_version=methodology_data["code_version"],
                    definition_hash=hashlib.sha256(
                        methodology_data["description"].encode("utf-8")
                    ).hexdigest(),
                )
            )
        release = session.scalar(
            select(SourceRelease).where(
                SourceRelease.source_id == source.id,
                SourceRelease.release_label == release_data["release_label"],
            )
        )
        if release is None:
            create_source_release(
                session,
                source_id=source.id,
                release_label=release_data["release_label"],
                source_url=release_data["source_url"],
                raw_storage_uri=release_data["raw_storage_uri"],
                raw_checksum_sha256=release_data["raw_checksum_sha256"],
                raw_record_count=release_data["raw_record_count"],
            )
        session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed explicit test-only fixtures.")
    parser.add_argument("--confirm-test-fixtures", action="store_true")
    arguments = parser.parse_args()
    if not arguments.confirm_test_fixtures:
        raise SystemExit("Refusing to seed without --confirm-test-fixtures.")
    seed_test_fixtures()


if __name__ == "__main__":
    main()
