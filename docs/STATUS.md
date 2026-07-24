# Readiness Status

## Current phase

Pre-USGS readiness gate complete on branch `agent/usgs-readiness`, based on
foundation commit `3f42f9c`. This phase makes the existing publication boundary
safe enough to accept the first real source adapter. It does not ingest or
publish the Alaska earthquake.

## Ground-truth reconciliation

### Confirmed

- The local repository is a Git repository on `agent/usgs-readiness`; `main`
  tracks `origin/main` at foundation commit `3f42f9c`.
- Alembic migrations form one forward chain from `20260723_0001` through
  `20260724_0006`
  (`services/api/alembic/versions/20260724_0006_methodology_quality_targets.py:10`).
- The schema contains every foundation table plus the three provenance joins.
  `publication_statement_evidence` maps one statement path to exactly one
  resolved claim or derived value
  (`services/api/alembic/versions/20260723_0002_publication_provenance.py:17`).
- Source releases are protected from update and deletion after ingestion
  (`services/api/alembic/versions/20260723_0001_foundation.py:410`).
- Final manifests, day profiles, and publication-statement mappings are
  immutable, and profile correction history is linear
  (`services/api/alembic/versions/20260723_0003_integrity_hardening.py:28` and
  `services/api/alembic/versions/20260723_0004_lifecycle_integrity.py:97`).
- The runtime API reads a published artifact through the local storage
  interface, verifies its content hash, returns an explicit unpublished state,
  and returns a storage-integrity error rather than reconstructing a profile
  (`services/api/app/main.py:172`).
- The frontend has loading, invalid-date, unpublished, API-error, and structured
  published states and keeps the seven product sections separate
  (`apps/web/src/components/DayProfileClient.tsx:18` and
  `apps/web/src/components/ProfileSections.tsx:23`).
- Foundation tests exist for empty-database migration, release immutability,
  claims, evidence cardinality, multiple event dates and locations,
  missing-versus-zero, publication hashes, immutable manifests and mappings,
  correction versioning, API unpublished behavior, and frontend unpublished
  behavior (`services/api/tests/test_migration.py:8`,
  `services/api/tests/test_publication.py:106`, and
  `apps/web/e2e/unpublished-state.spec.ts:3`).

### Contradicted

- `docs/STATUS.md` previously called the foundation complete, but the handoff
  also deferred the policy for mutation of provenance roots after publication.
  Real publication is therefore not safe yet
  (`docs/HANDOFF.md:137`; prior status claim was at `docs/STATUS.md:5`).
- `PublicationManifest.source_snapshot_hash` sounds evidence-derived, but
  `publish_day_profile` currently accepts any caller-provided 64-character
  value and stores it without calculating or verifying the evidence graph
  (`services/api/app/services.py:325`, `services/api/app/services.py:332`, and
  `services/api/app/services.py:369`).
- Existing publication tests prove manifest, profile, and mapping immutability;
  they do not prove that the resolved claim and imported claim state represented
  at publication remains recoverable after those live rows change
  (`services/api/tests/test_publication.py:106` and
  `services/api/tests/test_publication.py:132`).
- Before readiness, the frontend pinned Next.js `15.5.0` and React/React DOM `19.1.1`
  (`apps/web/package.json:22`). Those releases precede required upstream
  security fixes. The maintained 15.5 line must be upgraded before adding a
  public evidence page.
- The old foundation handoff predates the local Git initialization and initial
  GitHub push, so its repository-state section is no longer current.

### Could not determine before verification

- Whether the current checkout installs from the committed lockfiles in a clean
  environment.
- Whether the local database can be destroyed, recreated, and migrated from
  zero without drift.
- Whether all recorded foundation checks still pass on this branch.
- Whether the existing development database contains publication evidence rows.
  The readiness migration must refuse silent or fabricated snapshot backfill if
  such rows exist.
- Whether Next.js `15.5.21` and React `19.2.4` introduce a compatibility or type
  regression in this application; the build and browser suite must decide.

## Existing schema and migration state

- Current migration head before readiness work: `20260723_0004`.
- The schema correctly freezes source releases, published manifests, profile
  records, artifact bytes by storage contract, and statement mappings.
- Imported claims and resolved claims remain editable after publication.
  This is acceptable for an evolving working graph only if publication captures
  an immutable evidence snapshot of their exact published state.
- The migration suite creates PostGIS and `btree_gist` and is PostgreSQL-specific.
- The disposable integration-test database must end in `_test`; the fixture
  recreates it and applies every migration from empty state.

## Existing test state

- The prior phase recorded 30 passing Python tests, 1 contracts test, 3 web
  Vitest tests, 1 Playwright test, and a successful Next.js build.
- Those are historical results, not current verification. No readiness command
  has passed until it is recorded below with a timestamp.
- There is no test for a derived, canonical evidence snapshot or a manifest
  source-snapshot hash calculated from statement evidence.

## Existing API and frontend behavior

- API endpoints remain foundation-only: health, system status, methodologies,
  sources, and day profile.
- Supported but unpublished dates return `profile_not_published`.
- Out-of-range parsed dates return `date_out_of_supported_range`; malformed
  path dates are handled by FastAPI validation rather than a product-specific
  invalid-date body.
- Missing, corrupt, or hash-mismatched published artifacts return
  `profile_storage_unavailable`.
- The frontend renders the seven section shells, but published content currently
  supports only a generic statement and optional provenance note. The complete
  public-safe provenance control belongs to the USGS vertical slice.

## Defects that must be repaired before the vertical slice

1. Published statement evidence lacks an immutable snapshot of the exact claim,
   source release, source record locator, methodology, and evidence stance.
2. Manifest `source_snapshot_hash` is supplied by callers instead of calculated
   from canonical statement-evidence snapshots.
3. No test demonstrates that later working-graph edits leave the published
   evidence representation unchanged.
4. The Next.js and React dependency pins are below currently maintained security
   patch levels.
5. Documentation calls the foundation complete without making the deferred
   publication-root policy prominent enough for a real publication phase.

## Readiness implementation contract

### What this phase will create or change

- Add migration `20260723_0005` with required immutable evidence-snapshot JSON
  and SHA-256 columns on `publication_statement_evidence`.
- Make that migration fail clearly if legacy publication-evidence rows exist,
  because inventing a backfilled historical snapshot would be false provenance.
  The repository contract says no real profile exists yet, so the expected
  migration path is empty and deterministic.
- Add deterministic snapshot construction for resolved-claim and derived-value
  publication roots. Resolved snapshots include supporting and dissenting
  imported claims, immutable releases, source metadata, record locators,
  assertion state, resolution method/version, and methodology where present.
  Derived snapshots include their method and durable observation/resolution
  inputs.
- Calculate every evidence snapshot hash and the manifest source-snapshot hash
  from canonical JSON. Remove the caller-controlled hash argument.
- Add database and service tests proving snapshot construction, hash stability,
  dissent preservation, source checksum inclusion, and stability after working
  claim/resolution rows change.
- Upgrade the maintained frontend line to Next.js `15.5.21` and React/React DOM
  `19.2.4`, align related types/config packages, and regenerate the pnpm lockfile.
- Update architecture, lifecycle, data dictionary, decisions, README/status, and
  handoff documentation to match the implemented policy and current Git state.
- Recreate the local database from zero and run every documented foundation
  verification command.

### Why each change is required

- Evidence snapshots close the precise provenance-drift gap without freezing
  the entire working claim graph or weakening correction/version workflows.
- Derived hashes make the manifest an assertion about actual publication inputs,
  not a value a caller can fabricate.
- Migration refusal protects pre-existing published evidence from dishonest
  synthetic backfill.
- Dependency upgrades remove known upstream security exposure before the public
  page grows.
- Clean verification converts inherited status prose into current evidence.

### What this phase will not build

- USGS retrieval, fixtures, adapter interfaces, claims, resolution, quality
  grading, event/geography materialization, editorial review UI, golden profile,
  provenance drawer, admin endpoints, or publication commands.
- Any other source adapter or historical dataset.
- Production object storage, authentication, deployment, or licensing approval.
- A general ranking or scoring system.

### Acceptance criteria

- Every new publication-statement mapping stores canonical snapshot JSON and its
  SHA-256 hash before the manifest becomes published.
- Manifest `source_snapshot_hash` is deterministically calculated from the
  ordered statement paths and evidence hashes; callers cannot supply it.
- A published snapshot retains its original content and hash after permitted
  edits to working claim/resolution rows.
- Supporting and dissenting evidence, source identity, release identity, raw
  checksum, source-record locator, and methodology are present when applicable.
- Both resolved and derived publication roots have deterministic snapshots.
- A migration from an empty PostgreSQL/PostGIS database reaches the new head.
- The frontend uses Next.js `15.5.21` and React/React DOM `19.2.4` and passes
  lint, type checking, unit tests, browser tests, and production build.
- Documentation describes the same policy and commands as the implementation.
- No USGS fact or production profile is introduced during readiness.

### Verification commands

```bash
make install
make db-reset
make db-up
make api-migrate
make api-seed
make check
make web-e2e
make web-build
```

An isolated Uvicorn health request will also be run after migration. Exact
commands, timestamps, results, warnings, and resolved failures will be recorded
below and in `docs/HANDOFF.md`.

### Known risks

- Snapshot construction must remain deterministic across Python/database
  ordering and serialization; all collections therefore require explicit sort
  keys and canonical JSON hashing.
- Derived-value lineage can contain observations and nested resolved claims; the
  snapshot builder must reject missing roots rather than emit partial evidence.
- The migration intentionally blocks if legacy publication evidence exists.
  Such a database would require a separate forensic migration decision.
- `make db-reset` removes the local Docker database volume. This destructive
  reset is explicitly required and authorized by the readiness procedure.
- Package installation and Docker image acquisition require their external
  registries to be available.

## Completed work

- Read all governing documentation, migrations, runtime code, tests, frontend,
  shared contracts, configuration, fixtures, and repository instructions.
- Reconciled the handoff against executable behavior.
- Created and pushed the foundation checkpoint, then created
  `agent/usgs-readiness` for this gate.
- Recorded this implementation contract before production changes.
- Added migration `20260723_0005`, canonical resolved/derived evidence
  snapshots, statement snapshot hashes, and caller-independent manifest source
  snapshot hashing.
- Upgraded Next.js to `15.5.21` and React/React DOM to `19.2.4`.
- Fixed `make db-up` at the source so it waits for Docker health instead of
  allowing Alembic to race PostgreSQL's initialization restart.
- Reinstalled from empty dependency directories, recreated PostGIS from zero,
  applied all migrations, seeded test-only metadata, ran every automated gate,
  and completed an isolated API health request.
- Closed the PR review's five transitive evidence gaps: metric definitions,
  historical geography versions, applicable quality assessments, source
  lineage parents, and pipeline-run configuration. Added migration
  `20260724_0006` so methodologies can be quality-assessment targets without
  overloading the assessment-methodology relationship.

## In-progress work

- None.

## Blocked work

- None.

## Acceptance-criteria status

- Ground-truth reconciliation and contract: complete.
- Immutable evidence snapshot schema and service: complete.
- Caller-independent source snapshot hashing: complete.
- Snapshot integrity tests: complete.
- Dependency security upgrade: complete.
- Clean database recreation and migrations: complete.
- Full automated verification: complete.
- Documentation/handoff alignment: complete.

## Latest verification results

- `make install` from empty dependency locations: passed from
  `2026-07-23T23:30:31-05:00` through `23:30:40`. pnpm used the frozen lockfile;
  Python created a new virtual environment and installed the pinned API and dev
  dependencies. pnpm warned that build scripts for `esbuild`, `sharp`, and
  `unrs-resolver` were not approved; no required check or build failed.
- Initial combined `make db-reset && make db-up && make api-migrate` attempt:
  failed at `2026-07-23T23:30:51-05:00`. The original `db-up` returned during
  PostgreSQL's initialization restart, so the host connection closed before
  Alembic connected. No migration ran.
- Root-cause startup repair: `make db-up` now runs
  `docker compose up -d --wait db`. The rerun reported the container healthy,
  applied migrations `20260723_0001` through `20260723_0005` from zero, and
  completed `make api-seed` from `23:31:03` through `23:31:05`.
- `make check`: passed from `23:31:11` through `23:31:31`. Contracts lint,
  typecheck, and 1 Vitest test passed; Ruff passed; strict mypy reported no
  issues in 16 source files; pytest passed 32 tests; web lint, typecheck, and
  3 Vitest tests passed.
- `make web-e2e`: passed 1 Chromium test from `23:31:38` through `23:31:43`.
  The only warning was that `NO_COLOR` was ignored because `FORCE_COLOR` was
  set.
- `make web-build`: passed on Next.js `15.5.21` from `23:31:43` through
  `23:31:53`, including compilation, type validation, static generation, and
  route trace collection.
- Alembic current and isolated Uvicorn smoke test: passed from `23:32:04`
  through `23:32:05`; Alembic reported `20260723_0005 (head)` and
  `GET /health` returned `{"status":"ok"}`.
- Known unresolved verification failures: none.
- Review-fix gate at `2026-07-24T00:05:47-05:00`: stopped on Ruff import
  ordering before backend tests. Ruff's deterministic fix resolved it.
- Review-fix gate at `2026-07-24T00:06:01-05:00`: lint and types passed, then
  the new integration fixture correctly violated pipeline timestamp ordering
  because its generated start followed its explicit completion. Explicit,
  ordered fixture timestamps resolved it; production code was not implicated.
- Final review-fix `make check`: passed from `2026-07-24T00:06:33-05:00`
  through `00:06:52`. Ruff and strict mypy passed; pytest passed 33 tests;
  contracts and web lint, type checks, and Vitest suites passed.
