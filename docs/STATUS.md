# Foundation Status

## Current phase

Foundation complete. The repository is ready for a senior engineer to begin a
bounded, licensed, offline ingestion-to-publication vertical slice.

## Ground-truth reconciliation

### State before this phase

- `/home/juan-canfield/Desktop/day-perspective` did not exist.
- There were no project instructions, source files, package manifests,
  migrations, tests, fixtures, local infrastructure, or project documentation.
- The prompt's requested repository shape had no conflicting established
  structure to preserve.

### What now exists and works

- A pnpm monorepo with `apps/web`, `services/api`, and
  `packages/contracts`.
- A Next.js TypeScript date shell with separate recorded-event, typical-day,
  context, curated-claim, comparison, wonder/progress, and evidence sections.
- A FastAPI service with only the required health, metadata, and day-profile
  endpoints. Unpublished dates return an explicit `profile_not_published`
  response; no fixtures create historical facts.
- PostgreSQL/PostGIS Docker Compose infrastructure; SQLAlchemy models; Alembic
  migrations through `20260723_0004`; test-only fixture seeding; a local
  immutable JSON-profile storage implementation; and documented Make targets.
- Claim-first provenance tables, constrained vocabularies, raw checksums,
  source-release immutability, resolved-claim evidence, derived lineage,
  historical geography, multiple event dates/locations, publication hashes,
  correction chains, and publication statement evidence.
- Python, TypeScript, browser, migration, fixture, and runtime-health coverage
  with current passing results recorded below.

### Deliberate additions and deviations

- The requested repository shape is preserved. Root `package.json`, lockfile,
  environment examples, ignore files, and an `infra/postgres` initialization
  script are required operational support files.
- Three relational joins are added because the product requirements require
  them: `resolved_claim_evidence` retains supporting/dissenting claims,
  `derived_value_inputs` retains derivation inputs, and
  `publication_statement_evidence` maps every published JSON statement path to
  one provenance root.
- The migration history contains `20260723_0001` through
  `20260723_0004`. Revision `0004` was finalized before first application in
  this environment, so the live database advanced directly from `0003` to the
  finalized lifecycle-integrity revision.

### What remains absent by design

- Full external historical ingestion, production historical facts, all date
  profiles, ranking automation, a hardship score, accounts, social features,
  user claims, ancient-history coverage, AI-generated facts, live source calls,
  queues, vector/graph databases, deployment, and production storage.
- A policy for either transitive post-publication provenance-root freezing or
  immutable root snapshots. Releases, manifests, profile artifacts, and
  statement mappings are immutable now; the broader policy must be selected
  before a real profile is published.

### Assumptions carried into the foundation

- PostgreSQL with PostGIS is authoritative; ordinary rendering reads local
  metadata and immutable artifacts only.
- A resolved claim needs at least one supporting imported claim and may retain
  dissenting claims.
- Corrections form a linear successor chain for one date and profile type.
- Fixtures are clearly test-only and cannot be treated as historical evidence.

## Implementation contract

This contract was recorded before production scaffolding began and is now
fulfilled as stated below.

### What this phase created or changed

- Workspace, lockfile, Docker Compose, environment templates, Make targets,
  README, fixture commands, and clean local startup instructions.
- Next.js frontend and shared TypeScript contracts for the public date shell,
  loading/error states, unpublished state, and separated profile sections.
- FastAPI API skeleton, offline-only runtime boundary, filesystem profile-store
  interface, and no-fact fixture path.
- SQLAlchemy models and four forward Alembic migrations for every required
  foundational table and the minimum justified provenance joins.
- Lifecycle constraints for immutable releases/manifests/mappings, supporting
  resolution evidence, derived lineage, missing-versus-zero coverage, profile
  manifest hashes, correction identity, and linear successor chains.
- Product, architecture, lifecycle, data dictionary, decisions, status, and
  handoff documentation; targeted Python, TypeScript, and Playwright tests.

### Why the changes are required

- The stack and commands make a clean local environment reproducible.
- The schema makes claim-level provenance, disagreement, missingness,
  correction, and publication behavior enforceable rather than conventional.
- The API and frontend prove the intended rendering boundary without inventing
  historical facts.
- The contracts, fixtures, and tests give a successor model observable behavior
  to extend instead of commit archaeology to infer.

### What this phase did not build

- Any out-of-scope product or ingestion capability listed in
  `docs/PRODUCT_CONTRACT.md`.
- Real source-specific normalization, editorial operations, published facts, or
  real date-profile content.
- A final decision on post-publication mutation of the full provenance root
  graph; that deliberate senior-review decision is documented in `HANDOFF.md`.

### Acceptance criteria status

- Required repository shape and local developer commands: complete.
- Required documentation set and explicit product bands/deferrals: complete.
- Required foundational tables, constraints, ORM mappings, migrations, and
  data dictionary coverage: complete.
- Source-release, claim, evidence, derived-value, missingness, publication,
  correction, and successor-chain invariants: complete for this phase.
- Required API skeleton and honest unpublished response: complete.
- Required frontend shell, states, and visibly separate sections: complete.
- Required test categories, migration-from-empty coverage, and browser
  unpublished-state acceptance: complete.
- Real-source and post-publication-root lifecycle policy: deliberately deferred
  and explicitly handed off; not silently assumed complete.

### Verification commands

```bash
make install
make db-up
make api-migrate
make api-seed
make check
make web-e2e
make web-build
```

For an isolated API smoke check, start Uvicorn with `DATABASE_URL` on an unused
loopback port and request `GET /health`.

### Known risks

- Local filesystem profile storage has no production durability, authorization,
  backup, or multi-writer strategy.
- Post-publication roots need either transitive freezing or immutable snapshots
  before real content exists, as documented in `docs/HANDOFF.md`.
- The schema has not yet been exercised with a licensed historical source,
  real disagreement patterns, or real legal-review constraints.
- PostgreSQL trigger behavior is intentionally PostgreSQL/PostGIS-specific.
- `make db-reset` is deliberately destructive but explicitly named and warns;
  `make db-down` preserves local data.

## Completed work

- Initial repository inspection and pre-code reconciliation.
- Pre-implementation contract capture.
- Monorepo, database, API, frontend, contracts, docs, fixtures, and tests.
- Cold completion reviews of backend, frontend, docs, environment, migrations,
  tests, and runtime behavior.
- Root-cause lifecycle hardening: operation-specific affected-parent trigger
  routing, aligned supporting-evidence rules, and linear successor constraints.
- Final migration, seed, quality, browser, build, and API health verification.

## In-progress work

- None.

## Blocked work

- None.

## Completion review reconciliation

- Every added table, migration, command, and document is traceable to the
  implementation contract or an explicit local-operational requirement.
- No contract requirement is intentionally omitted except the explicitly
  deferred product scope and the documented post-publication-root policy.
- No change lacks contract justification.
- Tests prove user-visible unpublished behavior and lifecycle outcomes rather
  than only framework wiring; the operation matrix covers insert, delete, and
  retarget paths for the minimum-child invariants.
- Documentation now matches the implemented supporting-evidence rule, linear
  correction chain, migration head, offline-only path, and fixture behavior.
- No migration or startup failure remains. The early loopback-port collision
  and transient lint ordering issue were resolved before final verification.

## Latest verification results

- `make install`: passed earlier in this phase using the committed frozen pnpm
  lockfile and editable Python API install. No dependency manifests changed
  after that install.
- `docker compose up -d db`: passed; the local PostGIS database remains running
  for development and was not destructively reset.
- `make api-migrate`: passed at head `20260723_0004`.
- `make api-seed`: passed with both the explicit confirmation and
  `DAY_PERSPECTIVE_ALLOW_TEST_FIXTURES=1`; the raw fixture checksum is verified
  before seed metadata is inserted.
- `make check`: passed. Contracts lint/typecheck/test passed (1 Vitest test);
  Ruff passed; mypy reported no issues in 16 source files; pytest passed 30
  tests, including migration from an empty test database; web lint/typecheck
  passed and web Vitest passed 3 tests.
- `make web-e2e`: passed 1 Chromium Playwright test for the unpublished state
  and section separation.
- `make web-build`: passed Next.js 15.5.0 optimized production build.
- Isolated Uvicorn smoke test: passed with `GET /health` returning
  `{"status":"ok"}`.
- Known verification failures: none. Playwright emitted only an environment
  color-variable warning; it did not affect the passing browser test.
