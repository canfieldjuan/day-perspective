"""The first app-derived comparison (epic #51, UC4).

`derived_comparisons` has held content on zero of 27,759 dates. Everything
published so far is a source's assertion, resolved and selected; this is the
first claim the application itself makes, so it has to be unmistakably ours
and unmistakably bounded.

The comparison is deliberately dull: a year's count of active state-based
conflicts against the median of the reference period. The interesting work
is in what it refuses to say — no severity, no mortality, no trend, and
nothing at all for a year whose inputs are missing.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.base import LocalFilesystemRawSourceStore
from app.conflict_comparison import (
    COMPARISON_CALCULATION_VERSION,
    COMPARISON_MODEL_CARD,
    COMPARISON_VALUE_KIND,
    MINIMUM_REFERENCE_YEARS,
    cohort_fingerprint,
    derive_conflict_comparison,
    discrete_median,
    optional_conflict_comparison,
    reference_cohort,
)
from app.models import DerivedValue
from app.ucdp import ingest_ucdp_annual, review_ucdp_annual_release

from .helpers import synthetic_ucdp_multiyear_csv

# Twenty-one years, counts 1..21, so the discrete median is 11 and every
# year's difference is exactly known without restating the arithmetic. The
# size is deliberate: below MINIMUM_REFERENCE_YEARS no comparison is made at
# all, and that boundary needs a cohort on each side of it.
COHORT = [
    (str(900 + n), str(1970 + year_index))
    for year_index in range(21)
    for n in range(year_index + 1)
]
MEDIAN = 11

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


def test_the_discrete_median_is_an_observed_value() -> None:
    """A count is compared against a number some year actually recorded.

    An even cohort interpolates to a value that never happened — there is no
    year with 36.5 conflicts — and stating it invites a reader to check
    arithmetic that cannot be checked.
    """
    assert discrete_median([1, 2, 3, 4, 5]) == 3
    # Even cohort: the lower of the two central values, matching Postgres
    # percentile_disc. The real cohort has 80 years and central values 36 and
    # 37, so this convention is what makes the published baseline 36 — and
    # the choice is recorded in the model card rather than left implicit.
    assert discrete_median([13, 20, 40, 65]) == 20
    assert discrete_median([7]) == 7
    assert discrete_median([4, 3, 2, 1]) == 2, "order must not matter"


def test_the_cohort_fingerprint_changes_with_the_cohort() -> None:
    """The card promises a frozen cohort. The hash is what makes that
    checkable rather than asserted."""
    base = {1971: 1, 1972: 2}
    assert cohort_fingerprint(base) == cohort_fingerprint({1972: 2, 1971: 1})
    assert cohort_fingerprint(base) != cohort_fingerprint({1971: 1, 1972: 3})
    assert cohort_fingerprint(base) != cohort_fingerprint({1971: 1})


@pytest.mark.integration
def test_the_cohort_is_read_from_the_reviewed_release(
    session: Session, reviewed_cohort: UUID
) -> None:
    cohort = reference_cohort(session, reviewed_cohort)

    assert len(cohort) == 21
    assert cohort[1970] == 1
    assert cohort[1980] == 11
    assert cohort[1990] == 21


@pytest.mark.integration
def test_a_comparison_records_its_cohort_and_calculation_version(
    session: Session, reviewed_cohort: UUID
) -> None:
    derived = derive_conflict_comparison(
        session, year=1990, release_id=reviewed_cohort
    )

    assert derived is not None
    assert derived.value_kind == COMPARISON_VALUE_KIND
    assert derived.calculation_version == COMPARISON_CALCULATION_VERSION
    # 21 conflicts, above 20 of the 21 cohort years.
    assert int(derived.value_numeric or 0) == 95
    value = derived.value_json or {}
    assert value["count"] == 21
    assert value["percentile_rank"] == 95
    # Retained as context rather than published, so a reader of the record
    # can see both readings of the same cohort.
    assert value["reference_median"] == MEDIAN
    assert value["reference_period"] == [1970, 1990]
    assert value["cohort_size"] == 21
    assert value["cohort_sha256"] == cohort_fingerprint(
        reference_cohort(session, reviewed_cohort)
    )
    assert value["model_card"] == COMPARISON_MODEL_CARD


@pytest.mark.integration
def test_a_year_without_inputs_gets_no_comparison_at_all(
    session: Session, reviewed_cohort: UUID
) -> None:
    """Not zero, and not hedged into existence. A year the release does not
    cover has nothing to compare, and 'no difference from the median' would
    be a fabricated claim rather than a missing one."""
    # 2010 is outside the cohort entirely.
    assert (
        derive_conflict_comparison(session, year=2010, release_id=reviewed_cohort)
        is None
    )
    assert (
        optional_conflict_comparison(session, year=2010, statement_index=0) is None
    )
    assert (
        session.scalar(
            select(DerivedValue).where(
                DerivedValue.value_kind == COMPARISON_VALUE_KIND,
                DerivedValue.period_start == date(2010, 1, 1),
            )
        )
        is None
    )


@pytest.mark.integration
def test_deriving_a_comparison_twice_does_not_mint_a_second(
    session: Session, reviewed_cohort: UUID
) -> None:
    first = derive_conflict_comparison(
        session, year=1990, release_id=reviewed_cohort
    )
    second = derive_conflict_comparison(
        session, year=1990, release_id=reviewed_cohort
    )

    assert first is not None and second is not None
    assert first.id == second.id


@pytest.mark.integration
class TestTheStatementRefusesWhatItCannotSupport:
    """The comparison counts distinct conflicts. It does not measure how
    large they were, how many died, or where things are heading, and the
    published sentence must not imply otherwise."""

    def _statement(
        self, session: Session, release_id: UUID
    ) -> dict[str, Any]:
        derive_conflict_comparison(session, year=1990, release_id=release_id)
        content = optional_conflict_comparison(
            session, year=1990, statement_index=0
        )
        assert content is not None
        return content.statements[0]

    def test_it_states_the_count_the_rank_and_the_denominator(
        self, session: Session, reviewed_cohort: UUID
    ) -> None:
        text = str(self._statement(session, reviewed_cohort)["statement"])
        # A reader can place the number only if all three are present.
        assert "21 active state-based conflicts" in text
        assert "95%" in text
        assert "21 supported years" in text
        assert "1970" in text and "1990" in text

    def test_it_says_this_is_the_application_comparing(
        self, session: Session, reviewed_cohort: UUID
    ) -> None:
        statement = self._statement(session, reviewed_cohort)
        assert statement["provenance"]["root_type"] == "derived_value"
        assert (
            statement["details"]["model_card"] == COMPARISON_MODEL_CARD
        ), "no comparison ships without a card the reader can reach"

    def test_it_makes_no_claim_about_severity_or_mortality(
        self, session: Session, reviewed_cohort: UUID
    ) -> None:
        text = str(self._statement(session, reviewed_cohort)["statement"]).lower()
        for word in ("deaths", "died", "killed", "casualties", "deadlier",
                     "worse", "bloodier", "severity", "intensity"):
            assert word not in text, f"comparison implies {word!r}"

    def test_it_makes_no_claim_about_trend(
        self, session: Session, reviewed_cohort: UUID
    ) -> None:
        text = str(self._statement(session, reviewed_cohort)["statement"]).lower()
        for word in ("rising", "falling", "increasing", "declining", "trend",
                     "getting", "more violent", "peaceful"):
            assert word not in text, f"comparison implies {word!r}"

    def test_it_says_what_the_number_is_not(
        self, session: Session, reviewed_cohort: UUID
    ) -> None:
        text = str(self._statement(session, reviewed_cohort)["statement"])
        assert "describes the year, not this specific day" in text


@pytest.mark.integration
def test_too_short_a_reference_period_produces_no_comparison(
    session: Session, tmp_path: Path
) -> None:
    """A median over a handful of years is not a reference period.

    The committed fixture covers 1964 alone, so without this rule the golden
    profile would publish "25 conflicts, the same as the 1964-1964 median of
    25" — a value compared with itself, rendered as a finding.
    """
    short = [
        (str(900 + n), str(1970 + index))
        for index in range(MINIMUM_REFERENCE_YEARS - 1)
        for n in range(index + 1)
    ]
    fixture = tmp_path / "short.csv"
    fixture.write_text(synthetic_ucdp_multiyear_csv(short), encoding="utf-8")
    result = ingest_ucdp_annual(
        session,
        fixture_path=fixture,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )
    assert result.source_release_id is not None
    review_ucdp_annual_release(session, result.source_release_id)

    cohort = reference_cohort(session, result.source_release_id)
    assert len(cohort) == MINIMUM_REFERENCE_YEARS - 1, "one year below the floor"
    assert (
        derive_conflict_comparison(
            session, year=max(cohort), release_id=result.source_release_id
        )
        is None
    )


@pytest.mark.integration
def test_a_single_year_release_produces_no_comparison(
    session: Session, tmp_path: Path
) -> None:
    """The committed provenance canary's exact shape."""
    fixture = tmp_path / "one-year.csv"
    fixture.write_text(
        synthetic_ucdp_multiyear_csv([("900", "1964"), ("901", "1964")]),
        encoding="utf-8",
    )
    result = ingest_ucdp_annual(
        session,
        fixture_path=fixture,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )
    assert result.source_release_id is not None
    review_ucdp_annual_release(session, result.source_release_id)

    assert (
        derive_conflict_comparison(
            session, year=1964, release_id=result.source_release_id
        )
        is None
    )


@pytest.mark.integration
def test_the_cohort_ignores_counts_from_another_release(
    session: Session, reviewed_cohort: UUID, tmp_path: Path
) -> None:
    """A second release must not leak into the median.

    A fixture release sitting beside a full one is the ordinary development
    state, and an unscoped query takes the newest count per year across the
    whole database — assembling a baseline from a mixture of releases and
    publishing a number belonging to neither.
    """
    other = tmp_path / "other-release.csv"
    # Same years, wildly different counts, and a year the first lacks.
    other.write_text(
        synthetic_ucdp_multiyear_csv(
            [(str(700 + n), "1980") for n in range(40)]
            + [(str(700 + n), "2001") for n in range(5)]
        ),
        encoding="utf-8",
    )
    second = ingest_ucdp_annual(
        session,
        fixture_path=other,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw2"),
    )
    assert second.source_release_id is not None
    assert second.source_release_id != reviewed_cohort
    review_ucdp_annual_release(session, second.source_release_id)

    cohort = reference_cohort(session, reviewed_cohort)

    assert len(cohort) == 21, "the second release's years leaked in"
    assert 2001 not in cohort
    assert cohort[1980] == 11, "1980 took its count from the wrong release"


@pytest.mark.integration
def test_a_comparison_records_every_cohort_year_as_an_input(
    session: Session, reviewed_cohort: UUID
) -> None:
    """Durable lineage, not only a hash.

    The comparison is computed from the cohort's derived counts, so those
    are what its inputs must name. A hash proves the computation is
    reproducible; it does not let a reader walk the inputs, which
    docs/PRODUCT_CONTRACT.md requires to be inspectable.
    """
    from app.models import DerivedValueInput

    derived = derive_conflict_comparison(
        session, year=1990, release_id=reviewed_cohort
    )
    assert derived is not None

    rows = list(
        session.scalars(
            select(DerivedValueInput).where(
                DerivedValueInput.derived_value_id == derived.id
            )
        )
    )
    assert len(rows) == 21, "one input per cohort year"
    assert all(row.input_derived_value_id is not None for row in rows)
    roles = [row.input_role for row in rows]
    assert roles.count("primary") == 1
    assert roles.count("comparison") == 20

    # The subject input is the year's own count, not an arbitrary member.
    subject = next(row for row in rows if row.input_role == "primary")
    subject_value = session.get(DerivedValue, subject.input_derived_value_id)
    assert subject_value is not None
    assert subject_value.period_start.year == 1990


@pytest.mark.integration
def test_a_rerun_repairs_a_comparison_that_has_no_lineage(
    session: Session, reviewed_cohort: UUID
) -> None:
    """The idempotency branch returns before writing inputs, so a comparison
    derived before lineage existed could never gain it. Re-deriving must
    complete the record rather than only decline to duplicate it."""
    from app.models import DerivedValueInput

    derived = derive_conflict_comparison(
        session, year=1990, release_id=reviewed_cohort
    )
    assert derived is not None
    for row in session.scalars(
        select(DerivedValueInput).where(
            DerivedValueInput.derived_value_id == derived.id
        )
    ):
        session.delete(row)
    session.flush()

    again = derive_conflict_comparison(
        session, year=1990, release_id=reviewed_cohort
    )

    assert again is not None and again.id == derived.id, "must not duplicate"
    rows = list(
        session.scalars(
            select(DerivedValueInput).where(
                DerivedValueInput.derived_value_id == derived.id
            )
        )
    )
    assert len(rows) == 21, "the rerun did not restore the lineage"
