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
from app.conflict_comparison import derive_release_comparisons  # noqa: E402
from app.models import CoverageEntry  # noqa: E402
from app.services import LocalFilesystemPublishedProfileStore  # noqa: E402
from app.ucdp import ingest_ucdp_annual, review_ucdp_annual_release  # noqa: E402
from app.un_wpp import (  # noqa: E402
    ingest_un_wpp,
    publish_context_profile,
    review_un_wpp,
)

from .helpers import synthetic_ucdp_multiyear_csv  # noqa: E402

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
