from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from app.adapters.base import LocalFilesystemRawSourceStore
from app.config import get_settings
from app.database import SessionLocal
from app.wikidata import attempt_wikidata_enrichment, ingest_wikidata_candidate


def _ingest(args: argparse.Namespace, settings: Any, session: Any) -> str:
    result = ingest_wikidata_candidate(
        session,
        fixture_path=args.fixture,
        raw_store=LocalFilesystemRawSourceStore(settings.raw_source_root),
        dry_run=args.dry_run,
    )
    return (
        f"source_release_id={result.source_release_id} "
        f"claims={len(result.claim_ids)} idempotent={result.idempotent} "
        f"dry_run={result.dry_run}"
    )


def _enrich(args: argparse.Namespace, settings: Any, session: Any) -> str:
    outcome = attempt_wikidata_enrichment(session)
    return (
        f"status={outcome.status} occurrence_date={outcome.occurrence_date} "
        f"merge_review_task_id={outcome.merge_review_task_id} "
        f"colliding_manifest_id={outcome.colliding_manifest_id}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline candidate source pipelines.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser(
        "ingest", help="Ingest the pinned Wikidata candidate fixture."
    )
    ingest.add_argument("--fixture", type=Path, required=True)
    ingest.add_argument("--dry-run", action="store_true")
    ingest.set_defaults(handler=_ingest)

    enrich = subparsers.add_parser(
        "enrich",
        help="Attempt enrichment; defer to merge review on a recorded-event collision.",
    )
    enrich.set_defaults(handler=_enrich)

    args = parser.parse_args()
    settings = get_settings()
    with SessionLocal() as session:
        try:
            message = args.handler(args, settings, session)
        except Exception:
            # Persist the audit trail (e.g. the failed-ingestion pipeline run and
            # quality check) before surfacing the error.
            session.commit()
            raise
        session.commit()
        print(message)


if __name__ == "__main__":
    main()
