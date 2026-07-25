from __future__ import annotations

import argparse

from app.config import get_settings
from app.database import SessionLocal
from app.services import LocalFilesystemPublishedProfileStore, reconcile_publications


def main() -> None:
    parser = argparse.ArgumentParser(description="Publication maintenance operations.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    reconcile = subparsers.add_parser(
        "reconcile",
        help="Report, and optionally repair, interrupted publication state.",
    )
    reconcile.add_argument(
        "--repair",
        action="store_true",
        help="Apply recovery instead of reporting only.",
    )
    reconcile.add_argument(
        "--stale-temp-max-age-seconds",
        type=int,
        default=3600,
        help="Age after which an abandoned staging temp is swept.",
    )
    args = parser.parse_args()
    settings = get_settings()

    with SessionLocal() as session:
        if args.command == "reconcile":
            report = reconcile_publications(
                session,
                store=LocalFilesystemPublishedProfileStore(
                    settings.published_profile_root
                ),
                repair=args.repair,
                stale_temp_max_age_seconds=args.stale_temp_max_age_seconds,
            )
            session.commit()
            print(
                f"repair={str(args.repair).lower()} "
                f"completed_pending={report.completed_pending} "
                f"abandoned_pending={report.abandoned_pending} "
                f"missing_profiles={report.missing_profiles} "
                f"orphan_artifacts={report.orphan_artifacts} "
                f"hash_mismatches={report.hash_mismatches} "
                f"stale_temps_removed={report.stale_temps_removed} "
                f"healthy_published={report.healthy_published}"
            )
            for detail in report.details:
                print(f"detail={detail}")


if __name__ == "__main__":
    main()
