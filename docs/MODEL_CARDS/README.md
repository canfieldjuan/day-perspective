# Model Cards

## Approved for public use

- [`conflict-count-vs-reference-percentile-v2`](conflict-count-vs-reference-percentile-v2.md)
  — where a year's count of active state-based armed conflicts sits within
  the 1946–2025 cohort, as a percentile rank. Counts distinct conflicts
  only; explicitly not severity, mortality, or trend.

No other comparison model is approved.

## Superseded

- [`conflict-count-vs-reference-median-v1`](conflict-count-vs-reference-median-v1.md)
  — the same inputs expressed as a difference from the discrete median.
  Retained rather than deleted: it accurately describes what it published,
  and a superseded card is how a reader of an older artifact finds out what
  the number meant.

This directory is intentionally present so a comparison cannot be published
without a version-controlled model card. Each future card must identify inputs,
transformations, direction, weights, reference period, frozen cohort, cohort
hash, missing-data policy, minimum coverage, comparison count, sensitivity
analysis, and public language.

A universal historical badness score is forbidden for the MVP.
