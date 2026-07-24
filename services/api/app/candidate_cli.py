from __future__ import annotations

import argparse
from pathlib import Path

from app.adapters.base import LocalFilesystemRawSourceStore
from app.config import get_settings
from app.database import SessionLocal
from app.wikidata import ingest_wikidata_candidate


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline candidate source pipelines.")
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    with SessionLocal() as session:
        result = ingest_wikidata_candidate(
            session,
            fixture_path=args.fixture,
            raw_store=LocalFilesystemRawSourceStore(settings.raw_source_root),
            dry_run=args.dry_run,
        )
        session.commit()
        print(
            f"source_release_id={result.source_release_id} "
            f"claims={len(result.claim_ids)} idempotent={result.idempotent} "
            f"dry_run={result.dry_run}"
        )


if __name__ == "__main__":
    main()
