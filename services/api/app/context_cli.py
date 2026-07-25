from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import Source, SourceRelease
from app.ucdp import (
    UCDP_ANNUAL_URL,
    UCDP_GED_URL,
    UCDP_SOURCE_SLUG,
    ingest_ucdp_annual,
    ingest_ucdp_ged,
    review_ucdp_annual,
    review_ucdp_ged,
)
from app.un_wpp import (
    UN_WPP_SOURCE_SLUG,
    LocalFilesystemRawSourceStore,
    ingest_un_wpp,
    review_un_wpp,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline MVP context pipelines.")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest-un-wpp")
    source_mode = ingest.add_mutually_exclusive_group(required=True)
    source_mode.add_argument("--fixture", type=Path)
    source_mode.add_argument("--live", action="store_true")
    ingest.add_argument("--dry-run", action="store_true")
    commands.add_parser("review-un-wpp")
    ingest_ucdp_annual_parser = commands.add_parser("ingest-ucdp-annual")
    ingest_ucdp_annual_parser.add_argument("--fixture", type=Path, required=True)
    commands.add_parser("review-ucdp-annual")
    ingest_ucdp_ged_parser = commands.add_parser("ingest-ucdp-ged")
    ingest_ucdp_ged_parser.add_argument("--fixture", type=Path, required=True)
    commands.add_parser("review-ucdp-ged")
    args = parser.parse_args()
    settings = get_settings()
    with SessionLocal() as session:
        if args.command == "ingest-un-wpp":
            try:
                result = ingest_un_wpp(
                    session,
                    fixture_path=args.fixture if not args.live else None,
                    raw_store=LocalFilesystemRawSourceStore(settings.raw_source_root),
                    dry_run=args.dry_run,
                )
            except Exception:
                session.commit()
                raise
            session.commit()
            print(
                f"source_release_id={result.source_release_id} "
                f"claims={result.claim_count} idempotent={result.idempotent} "
                f"dry_run={args.dry_run}"
            )
            return
        if args.command in {"ingest-ucdp-annual", "ingest-ucdp-ged"}:
            raw_store = LocalFilesystemRawSourceStore(settings.raw_source_root)
            try:
                ucdp_result = (
                    ingest_ucdp_annual(
                        session,
                        fixture_path=args.fixture,
                        raw_store=raw_store,
                    )
                    if args.command == "ingest-ucdp-annual"
                    else ingest_ucdp_ged(
                        session,
                        fixture_path=args.fixture,
                        raw_store=raw_store,
                    )
                )
            except Exception:
                session.commit()
                raise
            session.commit()
            print(
                f"source_release_id={ucdp_result.source_release_id} "
                f"records={ucdp_result.record_count} "
                f"claims={ucdp_result.claim_count} "
                f"idempotent={ucdp_result.idempotent}"
            )
            return
        source_slug = (
            UN_WPP_SOURCE_SLUG
            if args.command == "review-un-wpp"
            else UCDP_SOURCE_SLUG
        )
        source = session.scalar(select(Source).where(Source.slug == source_slug))
        if source is None:
            raise ValueError(f"{source_slug} fixture has not been ingested.")
        source_url = (
            UCDP_ANNUAL_URL
            if args.command == "review-ucdp-annual"
            else UCDP_GED_URL
            if args.command == "review-ucdp-ged"
            else None
        )
        release = session.scalar(
            select(SourceRelease)
            .where(
                SourceRelease.source_id == source.id,
                *(
                    [SourceRelease.source_url == source_url]
                    if source_url is not None
                    else []
                ),
            )
            .order_by(SourceRelease.ingested_at.desc())
        )
        if release is None:
            raise ValueError(f"{source_slug} fixture has no source release.")
        if args.command == "review-un-wpp":
            count = review_un_wpp(session, release.id)
            label = f"reviewed_resolved_claims={count}"
        elif args.command == "review-ucdp-annual":
            derived = review_ucdp_annual(session, release.id)
            label = f"derived_value_id={derived.id}"
        else:
            event = review_ucdp_ged(session, release.id)
            label = f"event_id={event.id}"
        session.commit()
        print(label)


if __name__ == "__main__":
    main()
