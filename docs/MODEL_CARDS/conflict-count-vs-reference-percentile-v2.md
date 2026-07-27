# Conflict count vs reference percentile, v2

**Card ID:** `conflict-count-vs-reference-percentile-v2`
**Status:** Approved for public use, 2026-07-27 (epic #64, MD2)
**Calculation version:** `2.0.0`
**Value kind:** `conflict_count_vs_reference_percentile`
**Supersedes:** [`conflict-count-vs-reference-median-v1`](conflict-count-vs-reference-median-v1.md)

Supersedes rather than revises. v1 published a median *difference* and its
card remains accurate for what it published; this is a different statistic
and gets its own card.

## Public language

> **PERIOD COMPARISON**
> The selected date occurred during a year with **N** active state-based
> conflicts, ranking higher than **R%** of **C** supported years
> (**FIRST–LAST**).
>
> This comparison describes the year, not this specific day.

The order is deliberate. Leading with "higher than 95% of years" invites a
reader to supply their own subject, and the subject they supply is severity.
The sentence says what is being counted first.

## Transformations

```
rank = floor(100 × |{y in cohort : count(y) < count(Y)}| / |cohort|)
```

Three conventions, each of which would look defensible if got wrong and
would be wrong in a way nobody would notice:

1. **Strictly lower, not "lower or equal".** The sentence says *higher
   than*, so only years the subject genuinely exceeds are counted. Using ≤
   would let a year rank above itself, and every tied year would rank above
   every other.
2. **Ties share a rank.** Two years with identical counts have identical
   sets of strictly-lower years, so both report the same percentage. Any
   tie-break would invent an ordering the data does not contain and make
   two identical counts read as two different findings.
3. **Floor, never round.** At 74.6% the page says 74%, which is true.
   Rounding to 75% states something the cohort does not support.

The denominator is the whole cohort **including** the subject year, matching
the published phrase "of C supported years". A year is never strictly lower
than itself, so including it cannot inflate the rank — and no year can ever
reach 100%. The canary rejects any rank above 99% for exactly that reason:
seeing one means ties were counted as lower.

Worked against the real cohort: 2025 holds the period maximum (65) and ranks
**98%**. A year at the minimum ranks **0%**, and says so — omitting the
comparison there would leave the archive silent precisely where the number
is least flattering.

## Direction

Higher means **more distinct conflicts were recorded as active** in that
year than in that share of the reference period. It does not mean worse,
deadlier, larger, or more significant. Scale does not enter the computation
at any point.

## Weights

None. Every conflict-year record counts once regardless of intensity,
duration, participants, or deaths. A brief low-intensity conflict and a
decade-long war contribute equally. A deliberate limitation of a count-based
measure, not an oversight.

## Inputs, cohort, and coverage

Unchanged from v1: per-year counts of active state-based armed conflicts
(`active_state_based_conflict_count`) derived from UCDP/PRIO v26.1, source
release `383d339c-40de-44a6-aec2-2870262d258a`, scoped to that release.

- **Reference period:** 1946–2025, the full span of the pinned release.
- **Frozen cohort:** the 80 years in that release, fixed at publication. A
  new release requires a new card version.
- **Cohort hash:** SHA-256 over the sorted `(year, count)` pairs, recorded
  on the derived value and carried in the statement, so anyone can recompute
  the ranking and check they get the same one.
- **Lineage:** every cohort year is recorded as an input of the comparison,
  so the inputs can be walked rather than merely hashed.
- **Missing-data policy:** a year without a reviewed count receives no
  comparison at all — not zero, not a hedge.
- **Minimum coverage:** the subject year must be in the cohort, and the
  cohort must hold at least 20 years. Below that a rank is not meaningful:
  in a four-year cohort the steps are 25 points wide.

## Sensitivity analysis

The v1 baseline choice (discrete median 36 vs interpolated 36.5 vs mean
35.2) moved no year's direction. The percentile is likewise insensitive to
baseline definition because it uses none — it is a position within the
cohort rather than a distance from a summary of it.

What it **is** sensitive to is the cohort's composition, which is why the
cohort is frozen and hashed.

## What this must not be read as

1. **Not a severity or mortality measure.** The annual dataset records that
   a conflict was active, not how large it was or how many died.
   Battle-related deaths are a separate UCDP dataset covering 1989 onward;
   the two must never be conflated.

2. **Not a like-for-like comparison across the period — and a percentile
   makes this easier to misread than v1's difference did.** "Higher than
   74% of years" sounds like a ranking of how bad a year was. It ranks how
   many distinct conflicts were recorded, over a period in which the number
   of independent states roughly doubled. A state-based conflict requires a
   state, so later years draw on a larger pool the measure never divides by.
   Decade means run 18.0 in the 1940s to 58.3 in the 2020s, which reads as a
   threefold rise in violence and does not establish one. Expressing the
   same data as a rank compresses that confounding into a single confident
   number, so it is stated next to the number rather than in a footnote.

3. **Not a claim about trend.** A position within a period is not a
   direction of travel.

4. **Not a universal historical badness score.** Forbidden outright by
   `docs/MODEL_CARDS/README.md`, and nothing here may be assembled into one.

## Provenance

The statement's root is the derived value, so the evidence panel resolves to
the computation and its calculation version rather than to UCDP. The card's
identifier travels in the statement details and is rendered as a link — a
card nobody can reach is not disclosure.
