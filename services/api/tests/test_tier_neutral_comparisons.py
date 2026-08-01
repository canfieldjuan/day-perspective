"""Comparisons everywhere, enrichment nowhere (epic #64 / MD3, closes #62).

The operator's rule: a page becomes `partially_enriched` only when it holds
something tied to the *selected date*. Annual averages, annual conflict
counts and approved period comparisons are all `context_only`, however many
of them a page carries — otherwise every page becomes "enriched" because we
added another annual statistic, and the word becomes decorative.

`derived_comparisons` is an EDITORIAL_SECTION, so publishing a comparison
archive-wide would have promoted all 27,759 dates. The fix is not a special
case for comparisons: the tier counts date-specific content, which is the
operator's rule stated as code.

The dangerous half is the default. "Is this statement date-specific?" needs
an answer for content carrying no marker, and both answers are wrong in
different directions. Promotion is a claim — *this page holds something
about this day* — so it takes evidence, and unmarked content understates
rather than flatters.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.models import PublicationTier
from app.services import derive_publication_tier


def _payload(**sections: list[dict[str, Any]]) -> dict[str, Any]:
    base: dict[str, list[dict[str, Any]]] = {
        "recorded_on_this_date": [],
        "typical_day_in_this_year": [],
        "wider_historical_context": [],
        "curated_claims": [],
        "derived_comparisons": [],
        "wonder_and_progress": [],
        "evidence_notes": [],
    }
    base.update(sections)
    return {"sections": base}


def _period_statement(**details: Any) -> dict[str, Any]:
    marked: dict[str, Any] = {"temporal_assignment": "period_context"}
    marked.update(details)
    return {"statement_id": "period", "statement": "About the year.", "details": marked}


def _date_statement(**details: Any) -> dict[str, Any]:
    marked: dict[str, Any] = {"temporal_assignment": "direct_record"}
    marked.update(details)
    return {
        "statement_id": "dated",
        "statement": "About this day.",
        "details": marked,
    }


class TestTheTierCountsDateSpecificContent:
    def test_a_period_comparison_does_not_promote_a_page(self) -> None:
        """The whole point of the slice. Publishing this archive-wide would
        otherwise reclassify 27,759 context profiles as partially enriched,
        telling readers they hold curated content they do not."""
        payload = _payload(derived_comparisons=[_period_statement()])

        assert derive_publication_tier(payload) is PublicationTier.CONTEXT_ONLY

    def test_many_period_statements_still_do_not_promote(self) -> None:
        # "However many a page carries" — the count is not the question.
        payload = _payload(
            typical_day_in_this_year=[_period_statement()] * 3,
            wider_historical_context=[_period_statement()] * 4,
            derived_comparisons=[_period_statement()],
            curated_claims=[_period_statement(), _period_statement()],
            wonder_and_progress=[_period_statement()],
        )

        assert derive_publication_tier(payload) is PublicationTier.CONTEXT_ONLY

    def test_a_curated_claim_about_this_day_does_promote(self) -> None:
        """The rule is not "comparisons never promote" — it is that the tier
        counts content tied to the selected date. A dated curated claim is
        exactly what partially_enriched is meant to describe."""
        payload = _payload(curated_claims=[_date_statement()])

        assert derive_publication_tier(payload) is (
            PublicationTier.PARTIALLY_ENRICHED
        )

    def test_a_recorded_event_still_reaches_enriched(self) -> None:
        payload = _payload(
            recorded_on_this_date=[_date_statement()],
            derived_comparisons=[_period_statement()],
        )

        assert derive_publication_tier(payload) is PublicationTier.ENRICHED

    def test_an_annual_average_does_not_promote(self) -> None:
        """uniform_period_allocation is an annual total divided across days.
        It is the operator's canonical example of content that must stay
        context_only, and a deny-list of period markers would have let it
        promote."""
        payload = _payload(
            curated_claims=[
                _period_statement(temporal_assignment="uniform_period_allocation")
            ]
        )

        assert derive_publication_tier(payload) is PublicationTier.CONTEXT_ONLY

    def test_a_date_modeled_value_does_promote(self) -> None:
        # The operator lists "a date-specific modeled value" as promoting.
        payload = _payload(
            curated_claims=[
                _date_statement(temporal_assignment="modeled_period_allocation")
            ]
        )

        assert derive_publication_tier(payload) is (
            PublicationTier.PARTIALLY_ENRICHED
        )

    def test_an_unknown_assignment_does_not_promote(self) -> None:
        payload = _payload(
            curated_claims=[_date_statement(temporal_assignment="unknown")]
        )

        assert derive_publication_tier(payload) is PublicationTier.CONTEXT_ONLY

    def test_a_future_assignment_defaults_to_not_promoting(self) -> None:
        """An allow-list means a marker added to the enum later understates
        until somebody decides it should promote. A deny-list would promote
        it silently."""
        payload = _payload(
            curated_claims=[_date_statement(temporal_assignment="some_new_kind")]
        )

        assert derive_publication_tier(payload) is PublicationTier.CONTEXT_ONLY

    def test_editorial_context_is_period_content_too(self) -> None:
        payload = _payload(
            curated_claims=[_period_statement(temporal_assignment="editorial_context")]
        )

        assert derive_publication_tier(payload) is PublicationTier.CONTEXT_ONLY

    def test_an_explicit_date_specific_false_does_not_promote(self) -> None:
        """A statement can say so directly. The comparison's stored value
        carries date_specific: false, and honouring it costs nothing."""
        payload = _payload(
            curated_claims=[_date_statement(date_specific=False)]
        )

        assert derive_publication_tier(payload) is PublicationTier.CONTEXT_ONLY


class TestTheDefaultUnderstates:
    """Both answers to "is unmarked content date-specific?" are wrong in
    different directions, and only one of them is wrong quietly."""

    def test_unmarked_editorial_content_does_not_promote(self) -> None:
        # Defaulting the other way would let any unmarked statement promote
        # a page — the direction that silently reclassifies the archive.
        payload = _payload(curated_claims=[{"statement_id": "bare"}])

        assert derive_publication_tier(payload) is PublicationTier.CONTEXT_ONLY

    def test_a_malformed_details_map_does_not_promote(self) -> None:
        payload = _payload(
            curated_claims=[{"statement_id": "bad", "details": "not a map"}]
        )

        assert derive_publication_tier(payload) is PublicationTier.CONTEXT_ONLY

    def test_a_malformed_payload_still_degrades_to_the_modest_tier(self) -> None:
        assert derive_publication_tier({"sections": "nonsense"}) is (
            PublicationTier.CONTEXT_ONLY
        )
        assert derive_publication_tier({}) is PublicationTier.CONTEXT_ONLY

    def test_a_recorded_event_is_trusted_without_a_marker(self) -> None:
        """recorded_on_this_date is date-specific by construction — the
        section means it — so requiring a marker there would understate
        content the archive has already reviewed."""
        payload = _payload(recorded_on_this_date=[{"statement_id": "bare"}])

        assert derive_publication_tier(payload) is PublicationTier.ENRICHED


# --- The archive-wide half: a published comparison must move no signal ----

from datetime import date  # noqa: E402
from pathlib import Path  # noqa: E402
from uuid import UUID  # noqa: E402

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.adapters.base import LocalFilesystemRawSourceStore  # noqa: E402
from app.batch_publication import (  # noqa: E402
    CONTEXT_BATCH_KIND,
    run_context_batch,
    start_batch_run,
)
from app.conflict_comparison import derive_release_comparisons  # noqa: E402
from app.coverage import (  # noqa: E402
    coverage_entry,
    coverage_for_date,
    coverage_summary,
    random_enriched_date,
    rebuild_coverage_index,
)
from app.models import (  # noqa: E402
    CoverageEntry,
    PublicationManifest,
)
from app.services import LocalFilesystemPublishedProfileStore  # noqa: E402
from app.ucdp import ingest_ucdp_annual, review_ucdp_annual_release  # noqa: E402
from app.un_wpp import (  # noqa: E402
    ingest_un_wpp,
    publish_context_profile,
    review_un_wpp,
)
from app.usgs import GOLDEN_DATE  # noqa: E402

from .helpers import synthetic_ucdp_multiyear_csv  # noqa: E402
from .test_coverage_index import publish_enriched  # noqa: E402
from .test_usgs_vertical_slice import publish as publish_golden_vertical_slice  # noqa: E402

WPP_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "fixtures"
    / "un-wpp"
    / "wpp2024-world-1950-2025.csv"
)
# 1971-1991: twenty-one years, so the comparison clears its minimum.
PUBLISHED_DATE = date(1990, 6, 15)


@pytest.fixture()
def archive_with_comparisons(session: Session, tmp_path: Path) -> UUID:
    raw = LocalFilesystemRawSourceStore(tmp_path / "raw")
    wpp = ingest_un_wpp(session, fixture_path=WPP_FIXTURE, raw_store=raw)
    assert wpp.source_release_id is not None
    review_un_wpp(session, wpp.source_release_id)

    fixture = tmp_path / "cohort.csv"
    fixture.write_text(
        synthetic_ucdp_multiyear_csv(
            [
                (str(900 + n), str(1971 + index))
                for index in range(21)
                for n in range(index + 1)
            ]
        ),
        encoding="utf-8",
    )
    ucdp = ingest_ucdp_annual(session, fixture_path=fixture, raw_store=raw)
    assert ucdp.source_release_id is not None
    review_ucdp_annual_release(session, ucdp.source_release_id)
    derive_release_comparisons(session, ucdp.source_release_id)
    session.commit()
    return ucdp.source_release_id


@pytest.mark.integration
def test_a_published_comparison_leaves_the_page_context_only(
    session: Session, tmp_path: Path, archive_with_comparisons: UUID
) -> None:
    """The end-to-end form of the rule, through the real publisher rather
    than a hand-built payload."""
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")

    profile = publish_context_profile(
        session, store=store, profile_date=PUBLISHED_DATE
    )

    from app.models import PublicationManifest

    manifest = session.get(PublicationManifest, profile.publication_manifest_id)
    assert manifest is not None
    payload = store.read(manifest.storage_uri, manifest.content_hash)

    # The comparison is genuinely there ...
    assert len(payload["sections"]["derived_comparisons"]) == 1
    assert payload["section_states"]["derived_comparisons"] == {
        "status": "available"
    }
    # ... and the page is no richer for it.
    assert manifest.publication_tier is PublicationTier.CONTEXT_ONLY
    assert payload["publication_tier"] == "context_only"


@pytest.mark.integration
def test_a_comparison_moves_no_discovery_signal(
    session: Session, tmp_path: Path, archive_with_comparisons: UUID
) -> None:
    """Nearest-enriched, random-enriched, enriched counts and the recorded
    event flag all key off the index. Each is asserted rather than inferred
    from the tier holding — "it follows" is how regressions get in."""
    from app.coverage import coverage_for_date, coverage_summary, random_enriched_date

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    publish_context_profile(session, store=store, profile_date=PUBLISHED_DATE)
    publish_context_profile(
        session, store=store, profile_date=date(1990, 6, 16)
    )
    session.flush()

    entry = session.scalar(
        select(CoverageEntry).where(CoverageEntry.profile_date == PUBLISHED_DATE)
    )
    assert entry is not None
    assert entry.publication_tier is PublicationTier.CONTEXT_ONLY
    assert entry.has_recorded_event is False

    record = coverage_for_date(session, PUBLISHED_DATE)
    assert record is not None
    assert record.nearest_enriched_before is None
    assert record.nearest_enriched_after is None

    summary = coverage_summary(session)
    # The landing disclosure reads these. A comparison must not appear in
    # any bucket above context_only, nor in the recorded-event count.
    assert summary.by_tier.get("partially_enriched", 0) == 0
    assert summary.by_tier.get("enriched", 0) == 0
    assert summary.by_tier["context_only"] == summary.total_published
    assert summary.with_recorded_event == 0

    assert random_enriched_date(session) is None


# --- Archive-wide: the guarantee across a whole multi-year archive (MD4) -----

COHORT_YEARS = range(1971, 1992)  # 1971-1991, the fixture's twenty-one years
# Inside the cohort but distinct from every sampled context date below, so it
# has context dates both before and after it in the index.
ENRICHED_DATE = date(1981, 7, 4)


@pytest.mark.integration
def test_the_whole_archive_stays_context_only_when_comparisons_publish(
    session: Session, tmp_path: Path, archive_with_comparisons: UUID
) -> None:
    """MD3 proved a single comparison-bearing page stays context_only. MD4
    proves it across a whole multi-year archive published through the real
    batch path: the comparison attaches on every cohort date, no date is
    promoted, and nothing but the one genuinely enriched date is ever offered
    as a destination. Every guarantee is asserted over every indexed row,
    because "it follows from the tier" is how the last regressions got in.
    """
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")

    # One genuinely enriched date, published the way a recorded event is.
    publish_enriched(session, store, ENRICHED_DATE, label="golden-equivalent")

    # A representative spread of context dates across the whole cohort,
    # including both year boundaries, published through the same archive batch
    # path an operator's `publish-archive` runs.
    context_dates = [
        date(year, month, day)
        for year in COHORT_YEARS
        for (month, day) in ((1, 1), (6, 15), (12, 31))
    ]
    run = start_batch_run(
        session,
        kind=CONTEXT_BATCH_KIND,
        requested={"dates": [value.isoformat() for value in context_dates]},
    )
    report = run_context_batch(
        session, store=store, dates=context_dates, batch_run=run
    )
    assert report.failed == 0, report.failures
    rebuild_coverage_index(session)
    session.commit()

    entries = session.scalars(select(CoverageEntry)).all()
    by_date = {entry.profile_date: entry for entry in entries}

    # Pin membership first: a batch that silently skipped dates would let the
    # per-row guarantees below pass vacuously.
    assert set(by_date) == {ENRICHED_DATE, *context_dates}

    # (a) Every date except the enriched one is context_only with no recorded
    # event -- even though each cohort date carries a comparison.
    for profile_date, entry in by_date.items():
        if profile_date == ENRICHED_DATE:
            assert entry.publication_tier is PublicationTier.ENRICHED
            assert entry.has_recorded_event is True
        else:
            assert entry.publication_tier is PublicationTier.CONTEXT_ONLY
            assert entry.has_recorded_event is False
            # The comparison is genuinely attached on every cohort date.
            assert entry.sections["derived_comparisons"] == 1

    # ... and the served artifact really carries it, not just the index count.
    sample = date(1985, 6, 15)
    manifest = session.get(
        PublicationManifest, by_date[sample].publication_manifest_id
    )
    assert manifest is not None
    payload = store.read(manifest.storage_uri, manifest.content_hash)
    assert len(payload["sections"]["derived_comparisons"]) == 1
    assert payload["publication_tier"] == "context_only"

    # (c) Discovery keys off the tier and the recorded-event flag, so the one
    # enriched date is the only thing any of these can surface.
    summary = coverage_summary(session)
    assert summary.by_tier["enriched"] == 1
    assert summary.by_tier["partially_enriched"] == 0
    assert summary.by_tier["context_only"] == summary.total_published - 1
    assert summary.with_recorded_event == 1
    assert random_enriched_date(session) == ENRICHED_DATE

    # No comparison-bearing context date is ever offered as a destination:
    # from every context date the nearest richer date in either direction is
    # the enriched date or nothing at all, and the direction that holds it
    # points straight at it.
    for profile_date in by_date:
        if profile_date == ENRICHED_DATE:
            continue
        record = coverage_for_date(session, profile_date)
        assert record is not None
        assert record.nearest_enriched_before in (None, ENRICHED_DATE)
        assert record.nearest_enriched_after in (None, ENRICHED_DATE)
        assert record.nearest_recorded_event_before in (None, ENRICHED_DATE)
        assert record.nearest_recorded_event_after in (None, ENRICHED_DATE)
        if profile_date < ENRICHED_DATE:
            assert record.nearest_enriched_after == ENRICHED_DATE
        else:
            assert record.nearest_enriched_before == ENRICHED_DATE


@pytest.mark.integration
def test_the_golden_date_stays_enriched_in_the_index(
    session: Session, tmp_path: Path
) -> None:
    """The archive's one genuinely enriched profile. MD4's checklist requires
    proving 1964-03-27 keeps its enrichment status through a coverage rebuild
    -- asserted directly on the index here, which nothing did before (its
    enriched status was only proven at the API and e2e layers)."""
    publish_golden_vertical_slice(session, tmp_path)
    session.commit()

    rebuild_coverage_index(session)
    session.commit()

    entry = coverage_entry(session, GOLDEN_DATE)
    assert entry is not None
    assert entry.publication_tier is PublicationTier.ENRICHED
    assert entry.has_recorded_event is True
    assert entry.sections["recorded_on_this_date"] >= 1

    manifest = session.get(PublicationManifest, entry.publication_manifest_id)
    assert manifest is not None
    assert manifest.publication_tier is PublicationTier.ENRICHED

    # The single-year fixture has no twenty-year cohort, so no comparison
    # publishes on the golden date. The archive-wide comparison path and the
    # golden enriched path are exercised independently, as they run in
    # production (D039).
    assert entry.sections["derived_comparisons"] == 0
