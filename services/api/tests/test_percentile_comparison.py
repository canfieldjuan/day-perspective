"""Comparison model v2: percentile rank (epic #64 / MD2, closes #67).

v1 published a median difference. v2 publishes where the year sits within
the reference cohort, which is what a reader can actually place.

The statistic is easy; the honesty is in three conventions that would each
be defensible-looking if got wrong, and wrong in a way nobody would notice:

- **strictly lower**, because the sentence says *higher than*
- **ties share a rank**, because inventing an order the data lacks would
  make two identical counts read as different findings
- **floor, never round**, because "higher than 74%" must be true rather
  than generous

A percentile is also easier to misread than a difference was. "Higher than
74% of years" sounds like a ranking of how bad a year was; it ranks how many
distinct conflicts were recorded, in a period whose number of states roughly
doubled. The card carries that; these tests carry the arithmetic.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from app.adapters.base import LocalFilesystemRawSourceStore
from app.conflict_comparison import (
    COMPARISON_MODEL_CARD,
    percentile_rank,
)
from app.ucdp import ingest_ucdp_annual, review_ucdp_annual_release

from .helpers import synthetic_ucdp_multiyear_csv

# Twenty-one years with counts 1..21: every rank is exactly predictable.
COHORT = [
    (str(900 + n), str(1970 + index))
    for index in range(21)
    for n in range(index + 1)
]


@pytest.fixture()
def reviewed_cohort(session: Session, tmp_path: Path) -> UUID:
    fixture = tmp_path / "cohort.csv"
    fixture.write_text(synthetic_ucdp_multiyear_csv(COHORT), encoding="utf-8")
    result = ingest_ucdp_annual(
        session,
        fixture_path=fixture,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )
    assert result.source_release_id is not None
    review_ucdp_annual_release(session, result.source_release_id)
    return result.source_release_id


class TestPercentileRank:
    def test_the_lowest_year_ranks_above_nothing(self) -> None:
        """0% must render honestly rather than being hidden or floored into
        looking like a small positive rank."""
        assert percentile_rank(1, [1, 2, 3, 4]) == 0

    def test_the_highest_year_ranks_above_all_the_others(self) -> None:
        # 3 of 4 years are strictly lower: 75%, not 100%. A year is never
        # strictly higher than itself.
        assert percentile_rank(4, [1, 2, 3, 4]) == 75

    def test_ties_share_a_rank(self) -> None:
        """Two years with the same count have the same set of strictly-lower
        years. Any tie-break would invent an ordering the data does not
        contain, and make identical counts read as different findings."""
        cohort = [5, 5, 9, 1]
        assert percentile_rank(5, cohort) == percentile_rank(5, cohort)
        # One year (1) is strictly lower, out of four.
        assert percentile_rank(5, cohort) == 25

    def test_a_tie_counts_neither_year_as_above_the_other(self) -> None:
        # If ties counted as "lower or equal", each 5 would rank above the
        # other — both reported as higher than 75% of the cohort.
        assert percentile_rank(5, [5, 5, 9, 1]) != 75

    def test_it_floors_rather_than_rounds(self) -> None:
        """At 74.6% the page says 74%, which is true. Rounding to 75 states
        something the cohort does not support."""
        # 74 of 99 strictly lower = 74.747...%
        cohort = list(range(1, 100))
        assert percentile_rank(75, cohort) == 74

    def test_every_year_of_a_full_cohort_ranks_consistently(self) -> None:
        cohort = list(range(1, 22))
        ranks = [percentile_rank(value, cohort) for value in cohort]
        assert ranks == sorted(ranks), "rank must not decrease as count rises"
        assert ranks[0] == 0
        assert ranks[-1] == 95  # 20 of 21 strictly lower


@pytest.mark.integration
def test_the_published_sentence_states_the_rank_and_the_denominator(
    session: Session, reviewed_cohort: UUID
) -> None:
    from app.conflict_comparison import (
        derive_conflict_comparison,
        optional_conflict_comparison,
    )

    derive_conflict_comparison(session, year=1990, release_id=reviewed_cohort)
    content = optional_conflict_comparison(
        session, year=1990, statement_index=0
    )
    assert content is not None
    text = str(content.statements[0]["statement"])

    # 1990 holds 21 conflicts, above 20 of 21 years.
    assert "21 active state-based conflicts" in text
    assert "95%" in text
    assert "21 supported years" in text, "the denominator must be stated"
    assert "This comparison describes the year, not this specific day." in text


@pytest.mark.integration
def test_the_statement_carries_the_v2_card_and_a_derived_root(
    session: Session, reviewed_cohort: UUID
) -> None:
    from app.conflict_comparison import (
        derive_conflict_comparison,
        optional_conflict_comparison,
    )

    derive_conflict_comparison(session, year=1990, release_id=reviewed_cohort)
    content = optional_conflict_comparison(
        session, year=1990, statement_index=0
    )
    assert content is not None
    statement = content.statements[0]
    details = statement["details"]
    provenance = statement["provenance"]
    assert isinstance(details, dict) and isinstance(provenance, dict)

    assert details["model_card"] == COMPARISON_MODEL_CARD
    assert COMPARISON_MODEL_CARD.endswith("-v2")
    assert provenance["root_type"] == "derived_value"


@pytest.mark.integration
def test_the_lowest_year_says_zero_rather_than_going_quiet(
    session: Session, reviewed_cohort: UUID
) -> None:
    """A year at the bottom of the period is still a fact about it. Omitting
    the comparison there would leave the archive silent exactly where the
    number is least flattering."""
    from app.conflict_comparison import (
        derive_conflict_comparison,
        optional_conflict_comparison,
    )

    derive_conflict_comparison(session, year=1970, release_id=reviewed_cohort)
    content = optional_conflict_comparison(
        session, year=1970, statement_index=0
    )
    assert content is not None
    text = str(content.statements[0]["statement"])

    assert "0%" in text


@pytest.mark.integration
def test_it_still_refuses_severity_mortality_and_trend(
    session: Session, reviewed_cohort: UUID
) -> None:
    """The v1 refusals carry over. A percentile makes them matter more, not
    less: "higher than 95% of years" reads as a ranking of how bad the year
    was unless the sentence says what is being counted."""
    from app.conflict_comparison import (
        derive_conflict_comparison,
        optional_conflict_comparison,
    )

    derive_conflict_comparison(session, year=1990, release_id=reviewed_cohort)
    content = optional_conflict_comparison(
        session, year=1990, statement_index=0
    )
    assert content is not None
    text = str(content.statements[0]["statement"]).lower()

    for word in (
        "deaths",
        "died",
        "killed",
        "casualties",
        "deadlier",
        "worse",
        "bloodier",
        "severity",
        "rising",
        "falling",
        "trend",
        "more violent",
    ):
        assert word not in text, f"comparison implies {word!r}"
