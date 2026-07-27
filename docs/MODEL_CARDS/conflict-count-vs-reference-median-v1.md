# Conflict count vs reference median, v1

**Card ID:** `conflict-count-vs-reference-median-v1`
**Status:** Approved for public use, 2026-07-27 (epic #51, UC4)
**Calculation version:** `1.0.0`
**Value kind:** `conflict_count_vs_reference_median`

The first comparison Day Perspective publishes on its own account. Every
other published statement is a source's assertion, resolved and editorially
selected. This one is ours, so it is labelled **App-derived comparison** and
carries this card.

## Public language

> Day Perspective compares this: UCDP/PRIO records **N** state-based armed
> conflicts as active in **YEAR**, **D more/fewer than** the **FIRST–LAST**
> median of **M**. This is a count of distinct conflicts, not a measure of
> their scale.

A year equal to the median reads "the same as the FIRST–LAST median of M"
rather than reporting a difference of zero.

The sentence is generated from the stored values. It is never written by
hand per date, so it cannot drift from the number it describes.

## Inputs

Per-year counts of active state-based armed conflicts
(`active_state_based_conflict_count`), as reviewed and derived from
UCDP/PRIO Armed Conflict Dataset v26.1, source release
`383d339c-40de-44a6-aec2-2870262d258a`, covering 1946–2025.

The cohort is read from the **derived counts**, not the raw records, so the
comparison rests on exactly the reviewed values the per-year statements
publish.

## Transformations

`difference = count(year) − median(cohort)`

Integer arithmetic. No scaling, normalisation, smoothing, or weighting.

The median is the **discrete** median — the lower of the two central values
for an even cohort, matching Postgres `percentile_disc`. Not interpolated:
these are counts of conflicts, and interpolation produces a baseline no year
ever recorded. With 80 years and central values 36 and 37, the published
baseline is **36**, which splits the cohort exactly 40/40.

## Direction

Higher means **more distinct conflicts were recorded as active**. It does
not mean worse, deadlier, larger, or more significant. The scale of a
conflict does not enter the computation at any point.

## Weights

None. Every conflict-year record counts once, regardless of intensity,
duration, participants, or deaths. A brief low-intensity conflict and a
decade-long war contribute equally. This is a deliberate limitation of a
count-based measure, not an oversight.

## Reference period

1946–2025, the full span of the pinned release.

## Frozen cohort

The 80 years present in that release, with their reviewed counts. Fixed at
publication. A new UCDP release requires a new card version, because the
baseline would describe a different population.

## Cohort hash

SHA-256 over the canonical JSON of the sorted `(year, count)` pairs,
recorded on the derived value as `cohort_sha256` and carried in the
statement's details. Anyone can recompute the baseline and check they get
the same one; without this, "frozen cohort" would be an assertion rather
than a checkable fact.

## Missing-data policy

A year without a reviewed count receives **no comparison at all** — not
zero, not "no change", not a hedge. Absence is absence (D037). A page that
cannot compare says nothing about comparison.

## Minimum coverage

Two conditions, both required:

1. The year being described must be present in the cohort.
2. The cohort must contain at least **20 years**
   (`MINIMUM_REFERENCE_YEARS`). Below that a median is not a reference
   period: a one-year cohort compares a value with itself, and a handful of
   years gives a baseline that moves with every addition. Either way the
   sentence would still render with full confidence.

This threshold is editorial rather than statistical, which is why it is
stated here instead of left implicit in the code. It is also why the
committed single-year provenance fixture publishes no comparison at all —
the development and CI pipelines exercise the *absence* path, and only the
full release produces a comparison.

The cohort size and reference period are published alongside the number, so
a reader sees what the baseline was actually computed from rather than
trusting the stated span.

## Comparison count

One. This is the only approved comparison. No other may publish without its
own card.

## Sensitivity analysis

| Baseline | Value |
|---|---|
| Discrete median (published) | 36 |
| Interpolated median | 36.5 |
| Mean | 35.2 |

Cohort range 13–65. The choice among these three changes no year's
direction, so the baseline definition is not where the risk lives. The
limitation below is larger than all three differences by an order of
magnitude.

## What this must not be read as

1. **Not a severity or mortality measure.** The annual dataset records that
   a conflict was active, not how large it was or how many died.
   Battle-related deaths are a separate UCDP dataset covering 1989 onward.
   The two must never be conflated or presented as one series.

2. **Not a like-for-like comparison across the period.** This is the
   limitation that matters most. The number of independent states roughly
   doubled between 1946 and 2025, and a state-based conflict requires a
   state. Decade means run from 18.0 in the 1940s to 58.3 in the 2020s, and
   2025 is the maximum of the whole period — which reads as a world roughly
   three times more violent. It does not establish that. Much of the rise
   reflects a larger set of states, a denominator this measure does not
   divide by.

3. **Not a claim about trend.** One year against a median is a position, not
   a direction of travel.

4. **Not a universal historical badness score.** Forbidden outright by
   `docs/MODEL_CARDS/README.md`, and nothing here may be assembled into one.

## Provenance

The statement's root is the derived value, so the evidence panel resolves to
the computation and its calculation version rather than to UCDP. The card's
identifier travels in the statement details so the reader can reach it from
the page — a card nobody can find is not disclosure.
