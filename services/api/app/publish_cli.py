from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

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
from app.golden_canary import (
    GOLDEN_CANARY_KIND,
    CanaryValidation,
    plan_golden_canary,
    record_canary_publication,
    start_golden_canary_run,
)
from app.models import BatchRunStatus
from app.services import LocalFilesystemPublishedProfileStore, reconcile_publications

#: Repo-root-relative, resolved from this module: the CLI runs with its
#: working directory inside services/api.
GOLDEN_SET_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "golden-set"
    / "golden-dates-v1.json"
)


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
    canary = subparsers.add_parser(
        "golden-canary",
        help="Publish and validate the golden-100 dates a pipeline supports.",
    )
    canary.add_argument(
        "--golden-set",
        type=Path,
        default=GOLDEN_SET_PATH,
        help="Path to the golden-set file.",
    )
    canary.add_argument(
        "--resume",
        action="store_true",
        help="Continue the most recent canary run's unfinished and failed dates.",
    )
    canary.add_argument(
        "--validate-only",
        action="store_true",
        help="Re-check already-published canary dates without publishing.",
    )
    canary.add_argument(
        "--update-golden-set",
        action="store_true",
        help="Record validated dates as context_published in the golden set.",
    )
    canary.add_argument(
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
        elif args.command == "golden-canary":
            store = LocalFilesystemPublishedProfileStore(
                settings.published_profile_root
            )
            try:
                plan = plan_golden_canary(args.golden_set)
            except (OSError, ValueError) as error:
                raise SystemExit(str(error)) from error
            print(
                f"golden_dates={plan.total} publishable={len(plan.publishable)} "
                f"unsupported_era={len(plan.unsupported)}"
            )
            canary_report = None
            # What this invocation is responsible for. A resume finishes the
            # run it recorded, so it must also validate that run's dates:
            # validating a freshly-loaded golden set could pass on unrelated
            # pre-existing profiles while the resumed dates go unchecked.
            subject = plan.publishable
            if not args.validate_only:
                if args.resume:
                    run = latest_batch_run(session, kind=GOLDEN_CANARY_KIND)
                    if run is None:
                        raise SystemExit("No golden canary run exists to resume.")
                    recorded_dates = [
                        date.fromisoformat(str(value))
                        for value in (run.requested or {}).get("dates", [])
                    ]
                    if recorded_dates and recorded_dates != plan.publishable:
                        raise SystemExit(
                            "The golden set no longer matches the run being "
                            "resumed; finish or discard that run first."
                        )
                    subject = recorded_dates or plan.publishable
                    dates = outstanding_dates(session, batch_run=run)
                    # Finish the run as it was requested: resuming a forced
                    # republication without the flag would leave half the
                    # canary on a new version and half on the old one.
                    force_new_version = bool(
                        (run.requested or {}).get(
                            "force_new_version", args.force_new_version
                        )
                    )
                    run.status = BatchRunStatus.RUNNING
                    run.completed_at = None
                    session.commit()
                else:
                    dates = plan.publishable
                    force_new_version = args.force_new_version
                    run = start_golden_canary_run(
                        session, dates=dates, force_new_version=force_new_version
                    )
                canary_report = run_context_batch(
                    session,
                    store=store,
                    dates=dates,
                    batch_run=run,
                    force_new_version=force_new_version,
                )
                print(
                    f"batch_run_id={canary_report.batch_run_id} "
                    f"requested={canary_report.requested} "
                    f"published={canary_report.published} "
                    f"unchanged={canary_report.unchanged} "
                    f"skipped={canary_report.skipped} "
                    f"failed={canary_report.failed}"
                )
                for failed_date, reason in canary_report.failures:
                    print(f"failure date={failed_date.isoformat()} reason={reason}")
            validation = CanaryValidation.of(session, store=store, dates=subject)
            print(
                f"validated={validation.checked} "
                f"missing={len(validation.missing)} "
                f"dates_with_issues={len(validation.issues)}"
            )
            for profile_date in validation.missing:
                print(f"missing date={profile_date.isoformat()}")
            for _, issues in sorted(validation.issues.items()):
                for issue in issues:
                    print(f"issue {issue}")
            if args.update_golden_set:
                # A failed batch can still validate clean, because validation
                # reads whatever manifest is current — including an older one
                # the failed republish did not replace. Recording then claims
                # a canary that did not happen.
                if not validation.clean or (
                    canary_report is not None and canary_report.failed
                ):
                    raise SystemExit(
                        "Refusing to record canary publication: the run did "
                        "not complete cleanly."
                    )
                changed = record_canary_publication(args.golden_set, dates=subject)
                print(f"golden_set_updated={changed}")
            if not validation.clean or (
                canary_report is not None and canary_report.failed
            ):
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
