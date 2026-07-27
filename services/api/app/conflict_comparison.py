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
    Claim,
    ComparabilityStatus,
    DataStatus,
    DerivedValue,
    DerivedValueInput,
    Methodology,
    ResolvedClaimEvidence,
    SourceRelease,
    TemporalAssignment,
)
from app.services import PublicationStatementEvidenceInput

#: Version-controlled card in docs/MODEL_CARDS. No comparison publishes
#: without one, and the identifier travels in the statement so a reader can
#: reach it from the page rather than being told it exists.
COMPARISON_MODEL_CARD = "conflict-count-vs-reference-percentile-v2"
COMPARISON_VALUE_KIND = "conflict_count_vs_reference_percentile"
COMPARISON_CALCULATION_VERSION = "2.0.0"

# v1 published a median difference under value_kind
# conflict_count_vs_reference_median. Its rows are left in place and simply
# stop being read: the lookups below match on the v2 kind, so a v1 row can
# neither be served nor mistaken for a current one. Its card is retained and
# marked superseded, which is how a reader of an older artifact finds out
# what its number meant.
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
SCALE_DISCLAIMER = (
    "This comparison describes the year, not this specific day."
)

#: Said before the number, not after it. "Higher than 95% of years" reads as
#: a ranking of how bad a year was unless the sentence first says what is
#: being counted.
COUNT_SUBJECT = "active state-based conflicts"


@dataclass(frozen=True)
class ComparisonContent:
    statements: list[dict[str, object]]
    evidence: list[PublicationStatementEvidenceInput]
    derived_value_id: UUID


def percentile_rank(value: int, cohort: list[int]) -> int:
    """Where ``value`` sits in ``cohort``, as a whole percent.

    Three conventions, each of which would be defensible-looking if got
    wrong and wrong in a way nobody would notice:

    **Strictly lower.** The published sentence says *higher than*, so only
    years the subject genuinely exceeds may be counted. Counting ties as
    "lower or equal" would let a year rank above itself.

    **Ties share a rank.** Two years with the same count have the same set
    of strictly-lower years, so both report the same percentage. Any
    tie-break would invent an ordering the data does not contain and make
    two identical counts read as different findings.

    **Floor, never round.** At 74.6% the page says 74%, which is true.
    Rounding to 75% states something the cohort does not support.

    The denominator is the whole cohort including the subject year, matching
    the published phrase "of N supported years". A year is never strictly
    lower than itself, so including it cannot inflate the rank.
    """
    if not cohort:
        raise ValueError("A percentile requires a cohort.")
    strictly_lower = sum(1 for other in cohort if other < value)
    return (strictly_lower * 100) // len(cohort)


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


def _counts_from_release(session: Session, release_id: UUID) -> list[DerivedValue]:
    """Conflict counts derived from this release, newest first.

    Scoped by walking each count back to the claims it rests on. An
    unscoped query takes the newest count per year from the whole database,
    so a fixture release sitting beside a full one — which is the ordinary
    development state — would assemble a median from a mixture of releases
    and publish a number belonging to neither.
    """
    return list(
        session.scalars(
            select(DerivedValue)
            .join(
                DerivedValueInput,
                DerivedValueInput.derived_value_id == DerivedValue.id,
            )
            .join(
                ResolvedClaimEvidence,
                ResolvedClaimEvidence.resolved_claim_id
                == DerivedValueInput.resolved_claim_id,
            )
            .join(Claim, Claim.id == ResolvedClaimEvidence.claim_id)
            .where(
                DerivedValue.value_kind == CONFLICT_COUNT_KIND,
                Claim.source_release_id == release_id,
                ResolvedClaimEvidence.stance == "supporting",
            )
            .order_by(DerivedValue.created_at.desc())
            .distinct()
        )
    )


def reference_cohort(session: Session, release_id: UUID) -> dict[int, int]:
    """Every year's reviewed conflict count, as the comparison sees them.

    Reads the derived counts rather than the raw claims, so the cohort is
    made of the same reviewed values the per-year statements publish, and
    only those belonging to the requested release. A year that has not been
    reviewed is absent here, and absence propagates: the comparison refuses
    rather than quietly comparing against a partial period.
    """
    cohort: dict[int, int] = {}
    for derived in _counts_from_release(session, release_id):
        year = derived.period_start.year
        if year not in cohort and derived.value_numeric is not None:
            cohort[year] = int(derived.value_numeric)
    return cohort


def _cohort_values(
    session: Session, release_id: UUID
) -> dict[int, DerivedValue]:
    """The derived count backing each cohort year, for lineage rows."""
    values: dict[int, DerivedValue] = {}
    for derived in _counts_from_release(session, release_id):
        values.setdefault(derived.period_start.year, derived)
    return values


def _conflict_count_for(
    session: Session, year: int, release_id: UUID
) -> DerivedValue | None:
    for derived in _counts_from_release(session, release_id):
        if derived.period_start.year == year:
            return derived
    return None


def derive_conflict_comparison(
    session: Session, *, year: int, release_id: UUID
) -> DerivedValue | None:
    """Compare one year's conflict count with the reference median.

    Returns None where the year has no reviewed count. That is the whole
    point of the slice: a year we cannot compare produces no comparison at
    all, rather than a zero difference, which would be a claim we invented.
    """
    subject = _conflict_count_for(session, year, release_id)
    if subject is None or subject.value_numeric is None:
        return None
    cohort = reference_cohort(session, release_id)
    if year not in cohort or len(cohort) < MINIMUM_REFERENCE_YEARS:
        # Absent rather than degenerate. A comparison against too short a
        # period would still render as a confident sentence.
        return None

    counts = list(cohort.values())
    count = int(subject.value_numeric)
    rank = percentile_rank(count, counts)
    median = discrete_median(counts)
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
        # an identical computation — but they must still leave the lineage
        # complete. A comparison derived before input rows existed would
        # otherwise never gain them, since this branch returns first.
        _record_cohort_inputs(session, derived=existing, year=year, release_id=release_id)
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
        value_numeric=Decimal(rank),
        value_json={
            "year": year,
            "count": count,
            "percentile_rank": rank,
            # Retained as context rather than published. The median is what
            # v1 asserted, and keeping it lets a reader of the record see
            # both readings of the same cohort.
            "reference_median": median,
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

    _record_cohort_inputs(session, derived=derived, year=year, release_id=release_id)
    return derived


def _record_cohort_inputs(
    session: Session, *, derived: DerivedValue, year: int, release_id: UUID
) -> None:
    """Name every cohort year as an input of the comparison.

    Durable lineage, not just a hash. The comparison is computed from the
    cohort's derived counts, so those are what the inputs name — one row per
    year, with the year described distinguished from the reference set. The
    cohort hash proves the computation is reproducible; these rows let a
    reader walk it.

    Idempotent, so it can also repair a comparison derived before the rows
    existed.
    """
    already = set(
        session.scalars(
            select(DerivedValueInput.input_derived_value_id).where(
                DerivedValueInput.derived_value_id == derived.id
            )
        )
    )
    for cohort_year, cohort_derived in sorted(
        _cohort_values(session, release_id).items()
    ):
        if cohort_derived.id in already:
            continue
        session.add(
            DerivedValueInput(
                derived_value_id=derived.id,
                input_derived_value_id=cohort_derived.id,
                # The vocabulary the table already allows: the year being
                # described is the primary input, the rest are what it is
                # compared against.
                input_role="primary" if cohort_year == year else "comparison",
            )
        )
    session.flush()


def _sentence(value: dict[str, Any]) -> str:
    """Build the published sentence from the stored values.

    Every number is read back out of the derived value rather than passed
    alongside it, so the sentence cannot describe one computation while the
    provenance panel shows another.

    The order is deliberate: what was counted, then the rank, then the
    caveat. Leading with "higher than 95% of years" invites a reader to
    supply their own subject, and the one they supply is severity.
    """
    period = value["reference_period"]
    if not isinstance(period, list) or len(period) != 2:
        raise ValueError("A comparison must record its reference period.")
    # The year itself is not named: the page is that date, and "the selected
    # date occurred during a year with ..." reads from it. The period range
    # is named because a rank is meaningless without the cohort it ranks in.
    first, last = int(period[0]), int(period[1])
    count = int(value["count"])
    rank = int(value["percentile_rank"])
    cohort_size = int(value["cohort_size"])

    return (
        f"The selected date occurred during a year with {count} "
        f"{COUNT_SUBJECT}, ranking higher than {rank}% of {cohort_size} "
        f"supported years ({first}\u2013{last}). {SCALE_DISCLAIMER}"
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
            "value": value["percentile_rank"],
            "unit": "percent of supported years ranked below",
            "temporal_assignment": TemporalAssignment.PERIOD_CONTEXT.value,
            "data_status": derived.data_status.value,
            "comparability_status": derived.comparability_status.value,
            # Carried on the statement so the interface can link the card
            # from the page. A card nobody can reach is not disclosure.
            "model_card": COMPARISON_MODEL_CARD,
            "percentile_rank": value["percentile_rank"],
            "cohort_size": value["cohort_size"],
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
                f"{value['count']} active conflicts in {year} rank above "
                f"{value['percentile_rank']}% of {value['cohort_size']} "
                "years in the reference period."
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
