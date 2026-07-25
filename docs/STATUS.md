# Senior Takeover Status

Status time: 2026-07-24 20:48 CDT

## Next Slice Implementation Contract

### Root cause

The UN WPP adapter is fixture-shaped rather than release-shaped. It requires
exactly four hard-coded years, reviews exactly twenty claims, derives daily
equivalents only for 1964, accepts fixture input only, and renders profile
content only for March 27, 1964. Loosening any one constant would leave the
other layers inconsistent.

### This phase will change

- Add official WPP 2024 compact-workbook retrieval and parsing while preserving
  committed, network-independent normalized fixtures for tests.
- Import World aggregate records for every supported demographic year,
  `1950–2025`.
- Preserve whether a row is a WPP `Estimate` or `Medium` projection and map
  that distinction to explicit data status in claims, observations, coverage,
  derived values and public-safe details.
- Review and resolve five predicates per supported year without a hard-coded
  claim count.
- Derive average daily births and deaths for every supported year using the
  Gregorian day count and the existing uniform-allocation language.
- Parameterize UN profile-content construction by selected calendar date while
  retaining the existing 1964 publication behavior.
- Add fixture/live/dry-run CLI modes, idempotency and revision regression tests,
  and update commands and source documentation.

### Why these changes are required

The standard and enhanced profile contracts require annual demographic context
from 1950 through 2025. A production adapter must consume an immutable official
release, and profile construction must select reviewed evidence for the
requested year rather than a golden-date constant.

### This phase will not build

- New published day profiles or Golden 100 editorial approvals
- UCDP expansion
- Wikidata entity merging
- Apocalypse or wonder catalogs
- Comparison models
- Production authentication, deployment or object storage
- Human legal approval
- Deferred transaction-concurrency hardening tracked in GitHub issue #4

### Acceptance criteria

- Exactly one World aggregate record exists for each year `1950–2025`.
- `1950–2023` rows remain estimates and `2024–2025` rows remain projections.
- Each year produces five source claims, five resolved claims and two derived
  daily equivalents.
- Leap and non-leap years use the correct denominator.
- Missing or duplicate years, variants, records or measures fail before source
  release creation.
- Fixture tests remain independent of network access.
- Live retrieval records the official URL, retrieval time, exact release
  checksum and immutable raw workbook.
- A revised release produces current release-bound resolution and derived-input
  versions while preserving earlier versions; an unchanged checksum is
  idempotent.
- Profile content for a supported date selects that date's reviewed year and
  uses annual-equivalent language; unsupported years fail explicitly.
- Existing March 27, 1964 publication and frontend behavior remain unchanged.

### Verification commands

```bash
make db-up
make api-test
make api-lint
make api-typecheck
make check
make clean-reset
make ingest-un-wpp-fixture
make review-un-wpp-fixture
make publish-golden
make web-e2e-full-stack
```

Live retrieval is verified separately and is never part of automated tests.

### Known risks

- The official workbook is large and its human-readable header layout may
  change between revisions; schema validation must fail loudly.
- WPP 2024 changes from estimates to projections after 2023; collapsing those
  statuses would violate the product contract.
- The CC BY 3.0 IGO machine-readable gate does not replace human release
  approval.
- Full-year review creates hundreds of claims and review decisions; this phase
  must remain deterministic and idempotent without inventing batch heuristics.

## Current Phase

Full supported-year UN WPP vertical slice. The repository is not the contracted
MVP and is not production-ready.

## Ground Truth

- Branch: `agent/un-wpp-supported-years`
- Baseline commit: `51bc1b0de577d861a5baa28fa4e857df9ddfc5c2`
- Implementation commit: `c95b8db`
- GitHub publication: PR #2 and PR #3 are merged; this slice has no PR yet
- Migration head: `20260724_0011`
- Public shell: `1900-01-01` through `2025-12-31`
- Published coverage: only `1964-03-27`
- Golden set: 100 selected candidates, zero manually reviewed, zero generated
- Production deployment: none

## Completed Work

- Created `docs/SENIOR_TAKEOVER_AUDIT.md` before production changes.
- Added immutable release-level licensing, review decisions and editorial
  selections.
- Added a distinct `period_context` temporal assignment.
- Hardened the USGS adapter, review boundary, quality derivation and atomic
  published statements.
- Added UN WPP annual population, births, deaths, life expectancy and
  under-five mortality claims, observations, coverage and derived daily
  equivalents.
- Added UCDP/PRIO 1964 conflict-year context and one UCDP GED event fixture with
  bounded direct fatalities.
- Added Wikidata Q749610 candidate ingestion that creates review tasks and does
  not create resolved claims or canonical events.
- Added license snapshots for USGS, UN WPP, UCDP and Wikidata.
- Added a development-only review page and APIs for claims, releases,
  conflicts, tasks, manifests, publication and correction recording.
- Changed review decisions to use the append-only ledger and made resolution
  reject unreviewed candidates.
- Added a real browser-to-Next-to-FastAPI-to-artifact acceptance test.
- Added a Golden 100 selection validator that remains honestly red until human
  review and publication.
- Added CI, clean local reset, security review, backup/restore notes,
  deployment blockers and release checklist.
- Upgraded Next.js, Playwright, Sharp/PostCSS resolution, FastAPI, Starlette and
  pytest to remove known dependency vulnerabilities.
- Added `services/api/requirements.lock` and retained `pnpm-lock.yaml`.

## Verified Behavior

- Clean database recreation applied migrations `0001` through `0009`.
- USGS, UN WPP, UCDP annual, UCDP GED and Wikidata fixtures ingest offline.
- Golden publication reads three evidence releases: USGS, UN WPP and UCDP.
- The visible profile labels daily equivalents as annual-total calculations,
  not March 27 observations.
- The visible profile labels the UCDP value as 1964 period context, not a
  March 27 count.
- Public runtime reads and hash-verifies immutable artifacts.
- Real full-stack Playwright passes against FastAPI and the stored profile.
- JavaScript and Python dependency audits report no known third-party
  vulnerabilities.

## Verification Evidence

- 2026-07-24 18:42 CDT: review-hardening `make check` passed with Ruff,
  mypy, 101 Python tests, contracts checks and 10 frontend tests. The existing
  Starlette TestClient deprecation warning remains.
- `make clean-reset` through `make publish-golden`: passed at 13:50 CDT.
- `make check`: 71 Python tests, 1 contracts test and 5 web tests passed after
  dependency upgrades.
- `make web-build`: passed on Next.js 16.2.11.
- `make web-e2e`: 2 passed and the real-only spec correctly skipped.
- `make web-e2e-full-stack`: 1 passed.
- `make validate-golden-set`: selection shape passed; release readiness is
  false with 0 reviewed and 0 published.
- `pnpm audit --prod`: no known vulnerabilities.
- `pip-audit`: no known third-party vulnerabilities; the local editable
  package is not on PyPI and was skipped.
- `pip check`: no broken requirements.
- Known warning: Starlette 1.3.1 deprecates its `httpx` TestClient bridge in
  favor of `httpx2`; tests pass but migration remains future work.

## In Progress

- Documentation reconciliation and final diff reconstruction.

## Blocked or Missing MVP Work

- Full supported-year UN and UCDP pipelines
- Wikidata people, organizations, births, deaths, aliases, identifiers and
  merge workflows
- Curated apocalypse catalog
- Wonder and progress catalog
- Defensible frozen-cohort comparison models and model cards
- Generated and manually reviewed Golden 100 profiles
- A published limited-era and enhanced-era profile
- Public disagreement example
- Production authentication, object storage, deployment and observability
- Automated accessibility audit
- Human licensing review
- Backup/restore drill

## Acceptance Status

The evidence-to-publication standard-profile slice is green. The contracted MVP
release gates are red. Do not describe the repository as MVP complete or
technically ready for public launch.

## Final Senior Verification - 2026-07-24 14:18 CDT

- `make verify`: passed.
- Python: 76 tests passed; Ruff and mypy passed. One Starlette TestClient deprecation warning remains.
- TypeScript: contracts gates passed; 7 frontend tests passed; the Next.js production build passed.
- Browser: 2 mocked tests passed, the real-stack case was intentionally skipped there, and the separate real API/artifact/browser test passed.
- Golden-set structure: 100 records passed validation, but 0 are reviewed and 0 are published; release readiness remains false.
- Dependency audits: pnpm and pip-audit found no known third-party vulnerabilities; the local Python package is not published on PyPI and was skipped.

## Supported-Year UN WPP Slice Completion (2026-07-24 21:06 CDT)

### Completed Acceptance Criteria

- Official WPP 2024 compact workbook retrieval and strict schema parsing are implemented for live mode.
- The committed network-independent fixture contains World records for every supported year from 1950 through 2025.
- Estimates for 1950-2023 and medium-variant projections for 2024-2025 retain distinct data statuses through claims, observations, derived values, and public wording.
- Ingestion creates 76 immutable raw records and 380 atomic source claims; review creates 380 resolved claims and 152 daily-equivalent derived values.
- Fixture, live, and dry-run CLI modes are explicit; dry-run validation creates no source release.
- Requested-year profile content uses Gregorian leap-year denominators and never presents an annual equivalent as a date-specific observation.
- No additional profiles or datasets were added. Human licensing approval and deferred transaction-concurrency hardening remain outside this slice.

### Verification Results

- `TEST_DATABASE_URL=postgresql+psycopg://day_perspective:day_perspective@localhost:54329/day_perspective_test .venv/bin/python -m pytest -q services/api/tests/test_un_wpp.py`: passed, 14 tests in 23.86 seconds.
- `make check`: passed; contracts checks and 1 test, Ruff, strict mypy, 114 Python tests, web lint/typecheck, and 10 web tests passed. One Starlette TestClient deprecation warning remains.
- `make clean-reset`: passed and removed the prior database volume and local object roots.
- `make db-up db-migrate`: passed from zero through migration `20260724_0011`.
- USGS, full-range UN WPP, and UCDP annual fixture ingestion/review followed by `make publish-golden`: passed. Publication first failed closed, as designed, when UCDP had not yet been ingested.
- Published `day/1964-03-27/profile-v1.json` SHA-256: `1bf8f4ecdd97f7331b0fa659f6f052191ff5b5273483b2c7a514c206d579183b`.
- `make build test-integration`: passed; Next.js production build succeeded and 114 Python tests passed.
- `make web-e2e-full-stack`: passed, 1 Chromium test through the real browser/API/artifact path.

### Known Warnings and Remaining Gates

- Starlette warns that its httpx TestClient bridge is deprecated in favor of `httpx2`; this does not fail the current suite.
- The first two focused-test commands used the wrong local database endpoint and then wrong credentials; both failed before test execution and were resolved by using the documented Docker Compose PostGIS service.
- UN WPP publication remains subject to the recorded human legal-review gate.
- The Golden 100, full UCDP coverage, curated catalogs, comparison models, production authentication, deployment, and issue #4 transaction-concurrency hardening remain incomplete.
