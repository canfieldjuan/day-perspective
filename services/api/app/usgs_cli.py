from __future__ import annotations

import argparse
from pathlib import Path

from app.config import get_settings
from app.database import SessionLocal
from app.services import LocalFilesystemPublishedProfileStore
from app.usgs import (
    LocalFilesystemRawSourceStore,
    USGSEarthquakeAdapter,
    ingest_usgs,
    publish_golden_profile,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline USGS vertical-slice pipeline.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    ingest = subparsers.add_parser("ingest")
    ingest.add_argument("--fixture", type=Path)
    ingest.add_argument("--dry-run", action="store_true")
    subparsers.add_parser("review")
    subparsers.add_parser("publish")
    args = parser.parse_args()
    settings = get_settings()
    with SessionLocal() as session:
        if args.command == "ingest":
            result = ingest_usgs(
                session,
                adapter=USGSEarthquakeAdapter(),
                raw_store=LocalFilesystemRawSourceStore(settings.raw_source_root),
                fixture_path=args.fixture,
                dry_run=args.dry_run,
            )
            session.commit()
            print(
                f"checksum={result.checksum} idempotent={result.idempotent} "
                f"dry_run={result.dry_run}"
            )
        elif args.command == "review":
            from sqlalchemy import select

            from app.models import Source, SourceRelease
            from app.usgs import USGS_SOURCE_SLUG, accept_and_resolve_release

            source = session.scalar(select(Source).where(Source.slug == USGS_SOURCE_SLUG))
            if source is None:
                raise ValueError("USGS fixture has not been ingested.")
            release = session.scalar(
                select(SourceRelease)
                .where(SourceRelease.source_id == source.id)
                .order_by(SourceRelease.ingested_at.desc())
            )
            if release is None:
                raise ValueError("USGS fixture has no source release.")
            resolved = accept_and_resolve_release(session, release.id)
            session.commit()
            print(f"reviewed_resolved_claims={len(resolved)}")
        else:
            profile = publish_golden_profile(
                session,
                store=LocalFilesystemPublishedProfileStore(
                    settings.published_profile_root
                ),
            )
            session.commit()
            print(f"published_day_profile_id={profile.id}")


if __name__ == "__main__":
    main()
