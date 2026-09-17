from __future__ import annotations

import argparse
from pathlib import Path

from app.config import get_settings
from app.database import SessionLocal
from app.timezone_boundaries import seed_timezone_boundaries


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed the timezone boundary table for coordinates->timezone."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    seed = subparsers.add_parser("seed")
    seed.add_argument(
        "--fixture",
        type=Path,
        help="Load a local GeoJSON instead of the pinned release download.",
    )
    args = parser.parse_args()
    settings = get_settings()
    with SessionLocal() as session:
        count = seed_timezone_boundaries(
            session,
            url=settings.timezone_boundary_url,
            sha256=settings.timezone_boundary_sha256,
            dataset_version=settings.timezone_boundary_dataset_version,
            fixture_path=args.fixture,
        )
        session.commit()
    print(
        f"seeded timezone_boundaries rows={count} "
        f"dataset_version={settings.timezone_boundary_dataset_version}"
    )


if __name__ == "__main__":
    main()
