"""The first app-derived comparison (epic #51, UC4).

Everything the archive publishes so far is a source's assertion, resolved
and editorially selected. This is the first claim the application makes on
its own account, so it carries a different burden: the reader must be able
to tell that we computed it, check the arithmetic, and find the model card
that says what it must not be read as.

The comparison itself is deliberately dull — a year's count of active
state-based conflicts against the median of the reference period. The
substance is in the refusals. It says nothing about how large those
conflicts were, how many people died, or which direction history is
travelling, because the annual dataset supports none of those.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ComparabilityStatus,
    DataStatus,
    DerivedValue,
    Methodology,
    SourceRelease,
    TemporalAssignment,
)
from app.services import PublicationStatementEvidenceInput

#: Version-controlled card in docs/MODEL_CARDS. No comparison publishes
#: without one, and the identifier travels in the statement so a reader can
#: reach it from the page rather than being told it exists.
COMPARISON_MODEL_CARD = "conflict-count-vs-reference-median-v1"
COMPARISON_VALUE_KIND = "conflict_count_vs_reference_median"
COMPARISON_CALCULATION_VERSION = "1.0.0"
CONFLICT_COUNT_KIND = "active_state_based_conflict_count"

#: Below this, a median is not a reference period. A cohort of one year
#: compares a value with itself and reads as a finding; a handful of years
#: gives a baseline that moves with every addition. The threshold is
#: editorial, stated here and in the model card rather than left implicit,
#: and it is why the committed single-year fixture publishes no comparison.
MINIMUM_REFERENCE_YEARS = 20

#: What the sentence must always carry. The count is of distinct conflicts;
#: a reader who takes it for a measure of scale has been misled by us, not
#: by the source.
SCALE_DISCLAIMER = "This is a count of distinct conflicts, not a measure of their scale."


@dataclass(frozen=True)
class ComparisonContent:
    statements: list[dict[str, object]]
    evidence: list[PublicationStatementEvidenceInput]
    derived_value_id: UUID


def discrete_median(values: list[int]) -> int:
    """The lower of the two central values for an even cohort.

    Deliberately not an interpolated median. These are counts of conflicts,
    and interpolation produces a baseline no year ever recorded — there is
    no year with 36.5 conflicts. A reader invited to check the arithmetic
    should be checking it against something that happened.
    """
    ordered = sorted(values)
    if not ordered:
        raise ValueError("A median requires at least one value.")
    return ordered[(len(ordered) - 1) // 2]


def cohort_fingerprint(cohort: dict[int, int]) -> str:
    """A hash over the exact counts the median was taken from.

    The model card promises a frozen cohort. Without this the promise is an
    assertion; with it, anyone can recompute the baseline and see whether
    they get the same one.
    """
    canonical = json.dumps(
        [[year, cohort[year]] for year in sorted(cohort)],
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def reference_cohort(session: Session, release_id: UUID) -> dict[int, int]:
    """Every year's reviewed conflict count, as the comparison sees them.

    Reads the derived counts rather than the raw claims, so the cohort is
    made of the same reviewed values the per-year statements publish. A year
    that has not been reviewed is absent here, and absence propagates: the
    comparison refuses rather than quietly comparing against a partial
    period.
    """
    cohort: dict[int, int] = {}
    for derived in session.scalars(
        select(DerivedValue)
        .where(DerivedValue.value_kind == CONFLICT_COUNT_KIND)
        .order_by(DerivedValue.created_at.desc())
    ):
        year = derived.period_start.year
        if year not in cohort and derived.value_numeric is not None:
            cohort[year] = int(derived.value_numeric)
    return cohort


def _conflict_count_for(session: Session, year: int) -> DerivedValue | None:
    return session.scalars(
        select(DerivedValue)
        .where(
            DerivedValue.value_kind == CONFLICT_COUNT_KIND,
            DerivedValue.period_start == date(year, 1, 1),
        )
        .order_by(DerivedValue.created_at.desc())
    ).first()


def derive_conflict_comparison(
    session: Session, *, year: int, release_id: UUID
) -> DerivedValue | None:
    """Compare one year's conflict count with the reference median.

    Returns None where the year has no reviewed count. That is the whole
    point of the slice: a year we cannot compare produces no comparison at
    all, rather than a zero difference, which would be a claim we invented.
    """
    subject = _conflict_count_for(session, year)
    if subject is None or subject.value_numeric is None:
        return None
    cohort = reference_cohort(session, release_id)
    if year not in cohort or len(cohort) < MINIMUM_REFERENCE_YEARS:
        # Absent rather than degenerate. A comparison against too short a
        # period would still render as a confident sentence.
        return None

    median = discrete_median(list(cohort.values()))
    count = int(subject.value_numeric)
    difference = count - median
    fingerprint = cohort_fingerprint(cohort)

    existing = session.scalars(
        select(DerivedValue)
        .where(
            DerivedValue.value_kind == COMPARISON_VALUE_KIND,
            DerivedValue.period_start == date(year, 1, 1),
        )
        .order_by(DerivedValue.created_at.desc())
    ).first()
    if existing is not None and (existing.value_json or {}).get(
        "cohort_sha256"
    ) == fingerprint:
        # Same cohort, same answer. Reruns must not accumulate versions of
        # an identical computation.
        return existing

    methodology = session.get(Methodology, subject.methodology_id)
    if methodology is None:
        raise ValueError("The conflict count has no methodology to inherit.")

    derived = DerivedValue(
        metric_id=subject.metric_id,
        methodology_id=methodology.id,
        provenance_resolved_claim_id=subject.provenance_resolved_claim_id,
        value_kind=COMPARISON_VALUE_KIND,
        period_start=date(year, 1, 1),
        period_end=date(year, 12, 31),
        temporal_assignment=TemporalAssignment.PERIOD_CONTEXT,
        value_numeric=Decimal(difference),
        value_json={
            "year": year,
            "count": count,
            "reference_median": median,
            "difference": difference,
            "direction": (
                "same" if difference == 0 else "more" if difference > 0 else "fewer"
            ),
            "reference_period": [min(cohort), max(cohort)],
            "cohort_size": len(cohort),
            "cohort_sha256": fingerprint,
            "model_card": COMPARISON_MODEL_CARD,
            "date_specific": False,
        },
        data_status=DataStatus.FINAL,
        comparability_status=ComparabilityStatus.COMPARABLE,
        input_fingerprint=fingerprint,
        calculation_version=COMPARISON_CALCULATION_VERSION,
    )
    session.add(derived)
    session.flush()
    return derived


def _sentence(value: dict[str, Any]) -> str:
    """Build the published sentence from the stored values.

    Every number is read back out of the derived value rather than passed
    alongside it, so the sentence cannot describe one computation while the
    provenance panel shows another.
    """
    period = value["reference_period"]
    if not isinstance(period, list) or len(period) != 2:
        raise ValueError("A comparison must record its reference period.")
    first, last = int(period[0]), int(period[1])
    year = int(value["year"])
    count = int(value["count"])
    median = int(value["reference_median"])
    difference = abs(int(value["difference"]))
    direction = str(value["direction"])

    if direction == "same":
        relation = f"the same as the {first}–{last} median of {median}"
    else:
        plural = "conflict" if difference == 1 else "conflicts"
        relation = (
            f"{difference} {plural} {direction} than the "
            f"{first}–{last} median of {median}"
        )
    return (
        f"Day Perspective compares this: UCDP/PRIO records {count} state-based "
        f"armed conflicts as active in {year}, {relation}. {SCALE_DISCLAIMER}"
    )


def optional_conflict_comparison(
    session: Session, *, year: int, statement_index: int
) -> ComparisonContent | None:
    """This year's comparison as a publishable statement, or None.

    None means the year has no comparison to make. It is never a zero
    difference and never a hedge: a page that cannot compare says nothing
    about comparison at all.
    """
    derived = session.scalars(
        select(DerivedValue)
        .where(
            DerivedValue.value_kind == COMPARISON_VALUE_KIND,
            DerivedValue.period_start == date(year, 1, 1),
        )
        .order_by(DerivedValue.created_at.desc())
    ).first()
    if derived is None or derived.value_json is None:
        return None
    value = derived.value_json
    methodology = session.get(Methodology, derived.methodology_id)
    if methodology is None:
        return None
    release = session.scalars(
        select(SourceRelease).order_by(SourceRelease.ingested_at.desc())
    ).first()

    statement: dict[str, object] = {
        "statement_id": f"conflict-vs-median-{year}",
        "statement": _sentence(value),
        "details": {
            "title": f"Active conflicts in {year} against the reference median",
            "value": value["difference"],
            "unit": "conflict-year records",
            "temporal_assignment": TemporalAssignment.PERIOD_CONTEXT.value,
            "data_status": derived.data_status.value,
            "comparability_status": derived.comparability_status.value,
            # Carried on the statement so the interface can link the card
            # from the page. A card nobody can reach is not disclosure.
            "model_card": COMPARISON_MODEL_CARD,
            "reference_median": value["reference_median"],
            "reference_period": value["reference_period"],
            "cohort_sha256": value["cohort_sha256"],
            "missing_data_explanation": (
                "This comparison describes the year as a whole. It is not a "
                f"comparison of any single date in {year}."
            ),
        },
        "provenance_note": (
            "Computed by Day Perspective from reviewed UCDP/PRIO annual counts, "
            f"under model card {COMPARISON_MODEL_CARD}."
        ),
        "provenance": {
            "root_type": "derived_value",
            "published_statement": (
                f"{value['count']} active conflicts in {year} against a "
                f"reference median of {value['reference_median']}."
            ),
            "derived_value": {
                "kind": derived.value_kind,
                "calculation_version": derived.calculation_version,
                "value": value,
            },
            "supporting_claims": [],
            "dissenting_claims": [],
            "source_release": {
                "source": "Day Perspective (derived)",
                "publisher": "Day Perspective",
                "release": COMPARISON_MODEL_CARD,
                "source_url": f"docs/MODEL_CARDS/{COMPARISON_MODEL_CARD}.md",
                "raw_checksum_sha256": str(value["cohort_sha256"]),
                "retrieved_at": (
                    release.ingested_at.isoformat() if release is not None else ""
                ),
            },
            "methodology": {
                "name": methodology.name,
                "version": methodology.version,
                "description": methodology.description,
            },
        },
    }
    return ComparisonContent(
        statements=[statement],
        evidence=[
            PublicationStatementEvidenceInput(
                statement_path=f"/sections/derived_comparisons/{statement_index}",
                derived_value_id=derived.id,
            )
        ],
        derived_value_id=derived.id,
    )


def derive_release_comparisons(session: Session, release_id: UUID) -> int:
    """Derive every year's comparison, once the cohort is complete.

    Runs as its own pass after the review sweep rather than inside it: the
    median is taken over the whole reference period, so deriving a year's
    comparison before the last year is reviewed would compare it against a
    baseline that is about to change.

    Deriving is not publishing. These values exist so a comparison can be
    published from reviewed provenance; which pages carry one is a separate
    decision, and today only the golden profile does.
    """
    cohort = reference_cohort(session, release_id)
    derived = 0
    for year in sorted(cohort):
        if derive_conflict_comparison(session, year=year, release_id=release_id):
            derived += 1
    return derived
