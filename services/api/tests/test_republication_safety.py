"""Making the archive republication safe to run (epic #51, UC3a).

UC2 made every published date depend on its year's conflict context, which
turns two latent gaps into blockers for the republication pass.

Only a handful of years have ever been reviewed, and an unreviewed year now
fails its dates closed (D037), so the run needs a sweep that reviews the
whole release. And the context batch ledger records no source release at
all and resumes with no guard, so an interrupted run resumed after a
release moves would leave earlier dates on one release and later dates on
another — across 27,825 dates — and report success.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.base import LocalFilesystemRawSourceStore
from app.batch_publication import batch_run_is_resumable, context_batch_request
from app.models import DerivedValue
from app.ucdp import ingest_ucdp_annual, review_ucdp_annual_release

from .helpers import synthetic_ucdp_multiyear_csv

YEARS = ("1971", "1972", "1983")


@pytest.fixture()
def ingested_release(session: Session, tmp_path: Path) -> UUID:
    fixture = tmp_path / "multiyear.csv"
    fixture.write_text(
        synthetic_ucdp_multiyear_csv(
            [("900", "1971"), ("901", "1971"), ("900", "1972"), ("902", "1983")]
        ),
        encoding="utf-8",
    )
    result = ingest_ucdp_annual(
        session,
        fixture_path=fixture,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )
    assert result.source_release_id is not None
    session.commit()
    return result.source_release_id


def _derived_count(session: Session) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(DerivedValue)
            .where(
                DerivedValue.value_kind == "active_state_based_conflict_count"
            )
        )
        or 0
    )


@pytest.mark.integration
def test_the_sweep_reviews_every_year_the_release_covers(
    session: Session, ingested_release: UUID
) -> None:
    report = review_ucdp_annual_release(session, ingested_release)

    assert report.years_covered == (1971, 1972, 1983)
    assert report.reviewed == 3
    assert _derived_count(session) == 3


@pytest.mark.integration
def test_the_sweep_is_rerun_safe(
    session: Session, ingested_release: UUID
) -> None:
    """The republication pass may be interrupted and restarted, so a second
    sweep must not mint a second derivation for a year already reviewed."""
    review_ucdp_annual_release(session, ingested_release)
    before = _derived_count(session)

    second = review_ucdp_annual_release(session, ingested_release)

    assert _derived_count(session) == before
    assert second.reviewed == 0
    assert second.already_current == 3


@pytest.mark.integration
def test_the_sweep_names_a_year_it_could_not_review(
    session: Session, ingested_release: UUID, tmp_path: Path
) -> None:
    """A year that fails must be named and counted, never silently dropped:
    the republication would otherwise fail closed on every date of that year
    with no clue why."""
    from app.models import Claim
    from app.services import supersede_claim

    review_ucdp_annual_release(session, ingested_release)
    # Leave 1983 with an unreviewed successor claim.
    stale = session.scalar(
        select(Claim).where(
            Claim.source_release_id == ingested_release,
            Claim.assertion_json["year"].astext == "1983",
        )
    )
    assert stale is not None
    supersede_claim(
        session,
        prior_claim=stale,
        assertion_text="Unreviewed corrected conflict-year record.",
        assertion_json=stale.assertion_json,
    )
    session.flush()

    report = review_ucdp_annual_release(session, ingested_release)

    assert 1983 in dict(report.failures)
    assert report.failed == 1


class TestBatchReleasePinning:
    """The context batch ledger must record what it rested on.

    A predicate comparing a key the run never wrote would always pass, so
    both sides — the recording and the comparison — are covered.
    """

    DATES = [date(1971, 6, 15), date(1971, 6, 16)]
    WPP = UUID("11111111-1111-1111-1111-111111111111")
    UCDP = UUID("22222222-2222-2222-2222-222222222222")

    def _recorded(self, **overrides: object) -> dict[str, object]:
        recorded: dict[str, object] = {
            "dates": [value.isoformat() for value in self.DATES],
            "dry_run": False,
            "force_new_version": False,
            "source_release_id": str(self.WPP),
            "ucdp_source_release_id": str(self.UCDP),
        }
        recorded.update(overrides)
        return recorded

    def _current(self, **overrides: object) -> dict[str, UUID | None]:
        current: dict[str, UUID | None] = {
            "source_release_id": self.WPP,
            "ucdp_source_release_id": self.UCDP,
        }
        current.update(overrides)  # type: ignore[arg-type]
        return current

    def test_unchanged_inputs_resume(self) -> None:
        assert batch_run_is_resumable(
            self._recorded(),
            dates=self.DATES,
            current_releases=self._current(),
        )

    def test_a_moved_conflict_release_blocks_the_resume(self) -> None:
        assert not batch_run_is_resumable(
            self._recorded(),
            dates=self.DATES,
            current_releases=self._current(ucdp_source_release_id=uuid4()),
        )

    def test_a_moved_demographic_release_blocks_the_resume(self) -> None:
        assert not batch_run_is_resumable(
            self._recorded(),
            dates=self.DATES,
            current_releases=self._current(source_release_id=uuid4()),
        )

    def test_a_ledger_predating_release_pinning_still_resumes(self) -> None:
        """Every archive run recorded before this change carries no release
        keys. Treating that as a mismatch would strand them all behind a
        ledger there is no command to clear."""
        recorded = self._recorded()
        del recorded["source_release_id"]
        del recorded["ucdp_source_release_id"]

        assert batch_run_is_resumable(
            recorded, dates=self.DATES, current_releases=self._current()
        )

    def test_an_uningested_source_is_not_a_mismatch(self) -> None:
        assert batch_run_is_resumable(
            self._recorded(),
            dates=self.DATES,
            current_releases=self._current(ucdp_source_release_id=None),
        )

    def test_a_different_date_plan_blocks_the_resume(self) -> None:
        assert not batch_run_is_resumable(
            self._recorded(dates=["1990-01-01"]),
            dates=self.DATES,
            current_releases=self._current(),
        )


@pytest.mark.integration
def test_the_context_batch_records_both_releases_it_rests_on(
    session: Session, tmp_path: Path, ingested_release: UUID
) -> None:
    """The recording side. Without it the predicate above compares nothing."""
    request = context_batch_request(
        session,
        dates=[date(1971, 6, 15)],
        dry_run=False,
        force_new_version=False,
    )

    assert request["dates"] == ["1971-06-15"]
    assert request["ucdp_source_release_id"] == str(ingested_release)
    # No UN WPP release is ingested in this fixture, so the key is present
    # and null rather than absent — an absent key means "written before
    # pinning existed" and must keep meaning that.
    assert "source_release_id" in request
    assert request["source_release_id"] is None


def test_a_resume_with_no_date_plan_still_checks_the_releases() -> None:
    """A context resume finishes whatever run the ledger holds, so there is
    no plan to compare dates against. That must skip the date check without
    also skipping the release check — otherwise the guard passes always."""
    recorded: dict[str, object] = {
        "dates": ["1971-06-15"],
        "source_release_id": "11111111-1111-1111-1111-111111111111",
        "ucdp_source_release_id": "22222222-2222-2222-2222-222222222222",
    }
    current: dict[str, UUID | None] = {
        "source_release_id": UUID("11111111-1111-1111-1111-111111111111"),
        "ucdp_source_release_id": UUID("22222222-2222-2222-2222-222222222222"),
    }

    assert batch_run_is_resumable(recorded, dates=None, current_releases=current)
    assert not batch_run_is_resumable(
        recorded,
        dates=None,
        current_releases={**current, "ucdp_source_release_id": uuid4()},
    )
