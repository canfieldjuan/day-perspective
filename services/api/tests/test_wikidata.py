from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Claim, Event, ResolvedClaim, ReviewTask, SourceRelease
from app.wikidata import (
    LocalFilesystemRawSourceStore,
    ingest_wikidata_candidate,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "data/fixtures/wikidata/Q749610.json"


def test_wikidata_fixture_creates_candidates_not_accepted_facts(
    session: Session, tmp_path: Path
) -> None:
    result = ingest_wikidata_candidate(
        session,
        fixture_path=FIXTURE,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )

    assert result.source_release_id is not None
    assert len(result.claim_ids) == 8
    assert session.scalar(select(func.count()).select_from(ResolvedClaim)) == 0
    assert session.scalar(select(func.count()).select_from(Event)) == 0
    assert session.scalar(select(func.count()).select_from(ReviewTask)) == 8
    fatality = session.scalar(
        select(Claim).where(Claim.claim_type == "candidate_fatalities")
    )
    assert fatality is not None
    assert fatality.assertion_json is not None
    assert fatality.assertion_json["wikidata_reference_count"] == 0


def test_wikidata_fixture_is_idempotent_and_dry_run_persists_no_release(
    session: Session, tmp_path: Path
) -> None:
    store = LocalFilesystemRawSourceStore(tmp_path / "raw")
    dry = ingest_wikidata_candidate(
        session,
        fixture_path=FIXTURE,
        raw_store=store,
        dry_run=True,
    )
    assert dry.dry_run is True
    assert dry.source_release_id is None
    assert session.scalar(select(func.count()).select_from(SourceRelease)) == 0

    first = ingest_wikidata_candidate(
        session, fixture_path=FIXTURE, raw_store=store
    )
    second = ingest_wikidata_candidate(
        session, fixture_path=FIXTURE, raw_store=store
    )
    assert first.source_release_id == second.source_release_id
    assert second.idempotent is True
    assert session.scalar(select(func.count()).select_from(SourceRelease)) == 1
