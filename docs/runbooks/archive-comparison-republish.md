# Runbook: republish the archive with period comparisons

**Owner:** operator. **When:** once, to complete epic #64 / #62 (MD4) — attach
the approved period comparisons to every eligible date and rebuild the index.
Re-run any time the UCDP release or the comparison derivation changes.

This is **not** a CI step. CI stays offline on the committed single-year 1964
UCDP excerpt, which has no 20-year reference cohort and therefore publishes no
comparison (D039). The archive-wide guarantee is proven offline against a
synthetic multi-year fixture by
`services/api/tests/test_tier_neutral_comparisons.py`
(`test_the_whole_archive_stays_context_only_when_comparisons_publish`,
`test_the_golden_date_stays_enriched_in_the_index`). This runbook is the pass
that carries that proven behavior onto the real archive.

## What it does and does not change

- **Attaches** the derived period comparison to every eligible context date
  (a cohort year with reviewed conflict context that is not editorially
  declined for the date).
- **Does not** change any date's `publication_tier`: a comparison is annual
  content (`temporal_assignment=period_context`), which the tier does not
  count (D040). Every context date stays `context_only`.
- **Does not** touch the golden date `1964-03-27`: it is `enriched`, so the
  archive publisher preserves it (`richer_published_profile`).

## Preconditions

- Deploy the MD4 code first (D040 / the tier-neutral publish path merged).
- Production PostgreSQL/PostGIS reachable via `DATABASE_URL`, `PUBLISHED_PROFILE_ROOT` set.
- The real UCDP/PRIO v26.1 release ingested. If not:
  ```bash
  make ingest-ucdp-annual-live   # operator-only outbound fetch of pinned v26.1
  ```

## Steps

Run from the repo root. Each step is idempotent and safe to re-run.

1. **Derive the comparisons against the real release.**
   ```bash
   make review-ucdp-annual-all-years
   ```
   Reviews every year and runs `derive_release_comparisons` once the cohort is
   complete. **Confirm:** it reports one comparison per reference-cohort year
   (~80; the cohort needs at least 20 reviewed years).

2. **Republish the whole supported range.**
   ```bash
   make publish-archive ARGS="--force-new-version"
   ```
   Defaults to `FROM_YEAR=1950 TO_YEAR=2025`. Every context date is
   republished through the standard publish path, now attaching its year's
   comparison. `--force-new-version` regenerates uniformly rather than relying
   on content change alone; it is not what makes the comparison appear.
   **Confirm:** no failing years (the command exits non-zero and names any),
   a large published count, and the golden date reported `skipped`
   ("preserved a richer published profile"). The run is resumable if
   interrupted:
   ```bash
   make publish-context-resume        # finish a run that stopped partway
   make publish-context-retry-failed  # retry only the dates that failed
   ```

3. **Reconcile stored artifacts against the ledger.**
   ```bash
   make reconcile-publications          # report
   make reconcile-publications-repair   # only if step reports repairs
   ```
   **Confirm:** no outstanding discrepancies.

4. **Rebuild the coverage index.**
   ```bash
   make rebuild-coverage
   ```
   Regenerates the per-date index (tier, section counts, review status,
   quality floor, nearest-enriched/-recorded-event) from published state.
   **Confirm:** indexed count ≈ 27,759, nothing dropped, nothing unreadable.

## Confirmations to record on issue #62

Run these against the rebuilt real archive and paste the results into #62;
they are the archive-scale form of what the MD4a tests assert offline.

- **Tier buckets** (`coverage_summary`): `partially_enriched == 0`,
  `enriched == 1` (the golden date only), `context_only == total_published - 1`,
  `with_recorded_event == 1`.
- **Comparisons attached, tier neutral:** spot-check several context dates in
  cohort years — the served payload's `derived_comparisons` section is
  non-empty and `publication_tier == "context_only"`.
- **Discovery unmoved:** `random_enriched_date` and the `nearest_enriched_*`
  neighbours resolve only to `1964-03-27`.
- **Golden unchanged:** its coverage entry is `enriched` with
  `has_recorded_event == true`.
- **Gates green:**
  ```bash
  make golden-canary
  make validate-golden-set
  ```

When these pass, #62 and epic #64 can close.

## Known follow-up

The published comparison keeps its tier-neutrality through
`temporal_assignment=period_context` alone; its stored value also records
`date_specific: false`, but that flag is not carried into the published
`details`, so the independent `date_specific` guard never fires for the real
payload. Copying it in would add defense-in-depth, but it changes the payload
hash — so it belongs with a future republish, not a standalone change.
Tracked in #62 (see D040, "Known follow-up").
