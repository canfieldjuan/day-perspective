from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any
from uuid import UUID

from app.adapters.base import LocalFilesystemRawSourceStore
from app.config import get_settings
from app.database import SessionLocal
from app.governance import IdentityAdjudicationDecision
from app.services import LocalFilesystemPublishedProfileStore
from app.wikidata import (
    attempt_wikidata_enrichment,
    ingest_wikidata_candidate,
    publish_wikidata_event,
    resolve_merge_review,
    resolve_wikidata_event,
)


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
        f"colliding_manifest_id={outcome.colliding_manifest_id}"
    )


def _resolve(args: argparse.Namespace, settings: Any, session: Any) -> str:
    event = resolve_wikidata_event(session)
    return (
        f"event_id={event.id} event_type={event.event_type} "
        f"title={event.canonical_title!r}"
    )


def _publish(args: argparse.Namespace, settings: Any, session: Any) -> str:
    outcome = publish_wikidata_event(
        session,
        store=LocalFilesystemPublishedProfileStore(settings.published_profile_root),
        force_new_version=args.force_new_version,
    )
    return (
        f"status={outcome.status} occurrence_date={outcome.occurrence_date} "
        f"manifest_id={outcome.manifest_id} "
        f"colliding_manifest_id={outcome.colliding_manifest_id} "
        f"merge_review_task_id={outcome.merge_review_task_id}"
    )


def _adjudicate(args: argparse.Namespace, settings: Any, session: Any) -> str:
    """Record the human's answer to a merge-review collision.

    The command the whole merge-review workflow was missing: ``publish`` opens
    a task asking whether two events are the same event, and until this existed
    there was no way outside a test to answer it, so the collision deferred
    forever no matter what a reviewer decided.
    """
    recorded = resolve_merge_review(
        session,
        decision=IdentityAdjudicationDecision(args.decision),
        reviewer=args.reviewer,
        rationale=args.rationale,
        survivor_event_id=(
            UUID(args.survivor_event_id)
            if args.survivor_event_id is not None
            else None
        ),
    )
    return " ".join(
        f"adjudication_id={row.id} pair=({row.event_a_id},{row.event_b_id}) "
        f"decision={row.decision} version={row.decision_version}"
        for row in recorded
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline candidate source pipelines.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser(
        "ingest", help="Ingest the pinned Wikidata candidate fixture."
    )
    ingest.add_argument("--fixture", type=Path, required=True)
    ingest.add_argument("--dry-run", action="store_true")
    # Ingestion writes a failed-run audit trail that must survive an error.
    ingest.set_defaults(handler=_ingest, commit_on_error=True)

    enrich = subparsers.add_parser(
        "enrich",
        help="Attempt enrichment; defer to merge review on a recorded-event collision.",
    )
    enrich.set_defaults(handler=_enrich, commit_on_error=False)

    resolve = subparsers.add_parser(
        "resolve",
        help="Resolve the reviewed (accepted) Wikidata candidate into a canonical event.",
    )
    resolve.set_defaults(handler=_resolve, commit_on_error=False)

    publish = subparsers.add_parser(
        "publish",
        help=(
            "Publish the resolved, editorially-ranked Wikidata candidate as its "
            "date's recorded event, or defer on a recorded-event collision."
        ),
    )
    publish.add_argument("--force-new-version", action="store_true")
    publish.set_defaults(handler=_publish, commit_on_error=False)

    adjudicate = subparsers.add_parser(
        "adjudicate",
        help=(
            "Record a human's merge-review decision for the recorded-event "
            "collision on the candidate's date, and close the review task."
        ),
    )
    adjudicate.add_argument(
        "--decision",
        required=True,
        choices=[member.value for member in IdentityAdjudicationDecision],
    )
    adjudicate.add_argument(
        "--reviewer",
        required=True,
        help="The person recording this decision; a standing rule is refused.",
    )
    adjudicate.add_argument("--rationale", required=True)
    adjudicate.add_argument(
        "--survivor-event-id",
        default=None,
        help="The surviving event; required for merge and supersede only.",
    )
    adjudicate.set_defaults(handler=_adjudicate, commit_on_error=False)

    args = parser.parse_args()
    settings = get_settings()
    with SessionLocal() as session:
        try:
            message = args.handler(args, settings, session)
        except Exception:
            # Ingestion persists a failed-run audit trail (pipeline run + quality
            # check) that must survive the error; other commands have nothing to
            # preserve, so a partial write is rolled back rather than committed
            # (a half-resolved identity with no event would wedge later retries).
            if getattr(args, "commit_on_error", False):
                session.commit()
            else:
                session.rollback()
            raise
        session.commit()
        print(message)


if __name__ == "__main__":
    main()
