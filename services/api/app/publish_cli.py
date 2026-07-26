from __future__ import annotations

import argparse
from datetime import date

from app.batch_publication import (
    CONTEXT_BATCH_KIND,
    BatchPlanError,
    latest_batch_run,
    outstanding_dates,
    plan_context_dates,
    run_context_batch,
    start_batch_run,
)
from app.config import get_settings
from app.coverage import rebuild_coverage_index
from app.database import SessionLocal
from app.models import BatchRunStatus
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
    context = subparsers.add_parser(
        "publish-context",
        help="Publish context profiles for supported dates, resumably.",
    )
    selection = context.add_argument_group("date selection")
    selection.add_argument("--date", type=date.fromisoformat)
    selection.add_argument("--year", type=int)
    selection.add_argument("--from-date", type=date.fromisoformat)
    selection.add_argument("--to-date", type=date.fromisoformat)
    context.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan and ledger the run without publishing.",
    )
    context.add_argument(
        "--resume",
        action="store_true",
        help="Continue the most recent run's unfinished and failed dates.",
    )
    context.add_argument(
        "--retry-failed",
        action="store_true",
        help="Re-attempt only the most recent run's failed dates.",
    )
    context.add_argument(
        "--force-new-version",
        action="store_true",
        help="Publish a superseding version even when content is unchanged.",
    )
    subparsers.add_parser(
        "rebuild-coverage",
        help="Regenerate the coverage index from published state.",
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
        elif args.command == "publish-context":
            store = LocalFilesystemPublishedProfileStore(
                settings.published_profile_root
            )
            if args.resume or args.retry_failed:
                run = latest_batch_run(session, kind=CONTEXT_BATCH_KIND)
                if run is None:
                    raise SystemExit("No context batch run exists to resume.")
                dates = outstanding_dates(
                    session, batch_run=run, only_failed=args.retry_failed
                )
                # Resume the run as it was requested: a killed dry run must
                # not become a real publication because the resuming
                # invocation omitted the flag.
                recorded = run.requested or {}
                dry_run = bool(recorded.get("dry_run", args.dry_run))
                force_new_version = bool(
                    recorded.get("force_new_version", args.force_new_version)
                )
                run.status = BatchRunStatus.RUNNING
                run.completed_at = None
                session.commit()
            else:
                try:
                    dates = plan_context_dates(
                        single_date=args.date,
                        year=args.year,
                        from_date=args.from_date,
                        to_date=args.to_date,
                    )
                except BatchPlanError as error:
                    raise SystemExit(str(error)) from error
                dry_run = args.dry_run
                force_new_version = args.force_new_version
                run = start_batch_run(
                    session,
                    kind=CONTEXT_BATCH_KIND,
                    requested={
                        "dates": [value.isoformat() for value in dates],
                        "dry_run": dry_run,
                        "force_new_version": force_new_version,
                    },
                )
            batch_report = run_context_batch(
                session,
                store=store,
                dates=dates,
                batch_run=run,
                dry_run=dry_run,
                force_new_version=force_new_version,
            )
            print(
                f"batch_run_id={batch_report.batch_run_id} "
                f"requested={batch_report.requested} "
                f"published={batch_report.published} "
                f"unchanged={batch_report.unchanged} "
                f"skipped={batch_report.skipped} "
                f"failed={batch_report.failed}"
            )
            for failed_date, reason in batch_report.failures:
                print(f"failure date={failed_date.isoformat()} reason={reason}")
            if batch_report.failed:
                raise SystemExit(1)
        elif args.command == "rebuild-coverage":
            rebuild = rebuild_coverage_index(
                session,
                store=LocalFilesystemPublishedProfileStore(
                    settings.published_profile_root
                ),
            )
            session.commit()
            print(
                f"indexed={rebuild.indexed} dropped={rebuild.dropped} "
                f"unreadable={len(rebuild.unreadable)}"
            )
            for profile_date in rebuild.unreadable:
                print(f"unreadable date={profile_date.isoformat()}")
            if rebuild.unreadable:
                # An unservable date left out of the index is the honest
                # outcome, but it is not a clean rebuild.
                raise SystemExit(1)


if __name__ == "__main__":
    main()
