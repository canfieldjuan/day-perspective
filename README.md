# Day Perspective

Day Perspective is a provenance-first historical perspective web-app foundation.
It is deliberately not an "on this day" trivia app: every eventual public
statement must be traceable through a resolved or derived claim, immutable source
release, raw record, methodology/code version, and publication manifest.
Publication captures a canonical, hashed evidence snapshot for every statement,
so later working-graph corrections cannot rewrite what an earlier version meant.

See [docs/PRODUCT_CONTRACT.md](docs/PRODUCT_CONTRACT.md) for the product scope,
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the data/runtime boundary, and
[docs/HANDOFF.md](docs/HANDOFF.md) for continuation guidance.

## Prerequisites

- Node `20.20.x` and Corepack (the workspace pins pnpm `10.14.0`).
- Python `3.13.x`.
- Docker Compose with network access to pull `postgis/postgis:16-3.5` the first
  time.

## Clean install and local startup

```bash
cd /home/juan-canfield/Desktop/day-perspective
cp .env.example .env
cp apps/web/.env.example apps/web/.env.local
corepack enable
make install
make db-up
make api-migrate
make api-seed
```

Start the API in one terminal:

```bash
make api-run
```

Start the frontend in another:

```bash
make web-run
```

Open `http://localhost:3000`. The fixture seed creates no events or profiles, so
a supported date correctly displays `profile_not_published` rather than fake
historical content.

## Database and fixture commands

```bash
make db-up
make api-migrate
make api-seed
make db-down
```

`make db-down` stops the local database and preserves its volume. `make db-reset`
is the explicit destructive command that removes all local PostgreSQL data.
`make db-up` waits for the declared PostgreSQL health check before returning, so
the next migration command does not race database initialization.

The fixture command requires its explicit CLI confirmation plus the Makefile's
test-fixture opt-in environment flag, and reads only
`data/fixtures/test_only_seed.json` and its checksum-verified raw fixture
artifact. It is not production seed data.

## Checks

```bash
make check
make web-e2e
make web-build
```

`make check` runs shared-contract lint/type/test, API Ruff/mypy/pytest, and
web lint/typecheck/Vitest. Browser acceptance and the production build remain
explicit commands because they require a browser binary and more setup time.

`make api-test` creates, migrates, and removes the disposable database named by
`TEST_DATABASE_URL`; the name must end in `_test`. Run `make db-up` first.

`make install` uses the committed lockfile. To run the JavaScript installation directly:

```bash
corepack pnpm install --frozen-lockfile
```

Playwright may need its browser binary installed once:

```bash
corepack pnpm --filter @day-perspective/web exec playwright install chromium
```

## API surface in this phase

- `GET /health`
- `GET /api/v1/system/status`
- `GET /api/v1/methodologies`
- `GET /api/v1/sources`
- `GET /api/v1/day/{yyyy-mm-dd}`

The day endpoint responds with a clear `profile_not_published` payload for an
in-range date without a published profile. It never queries external sources at
request time.

## Intentionally not built yet

No external historical ingestion, full profile publication, production data,
universal score, live APIs, user features, queues, vector/graph DB, or deployment
is included. The foundation is designed for a senior engineer to build those
capabilities without losing provenance or reconstructing intent.

## Current senior-takeover state

The repository has one working standard-profile slice for `1964-03-27`.
It combines reviewed USGS earthquake evidence, UN WPP annual demographic
context and UCDP annual conflict context. Wikidata ingestion is candidate-only.
The Golden 100 is selected but not reviewed or generated. The contracted MVP
is **not complete**; see `docs/RELEASE_CHECKLIST.md`.

## Multi-source golden slice

The repository now proves one offline evidence-to-publication chain for the March 27, 1964 Alaska earthquake. Automated tests use only the committed official response in `data/fixtures/usgs/1964-prince-william-sound.geojson`; ordinary page requests never contact USGS.

```bash
make clean-reset
make db-up
make db-migrate
make ingest-usgs-fixture
make review-usgs-fixture
make ingest-un-wpp-fixture
make review-un-wpp-fixture
make ingest-ucdp-annual-fixture
make review-ucdp-annual-fixture
make ingest-ucdp-ged-fixture
make review-ucdp-ged-fixture
make ingest-wikidata-fixture
make publish-golden
```

The published development artifact is `.local/published-profiles/day/1964-03-27/profile-v1.json`. Start the services in separate terminals:

```bash
make api
make web
```

Open `http://localhost:3000/day/1964-03-27`. Live official retrieval is an explicit offline operation:

```bash
make ingest-usgs-dry-run
make ingest-usgs-live
```

A changed response creates a new immutable source release. Do not use live
ingestion in automated tests. UN WPP, UCDP and Wikidata currently support the
committed fixture path only.

The minimal review API is development-only. Send `X-Development-Review-Token` with the value configured by `DEVELOPMENT_REVIEW_TOKEN` to `/api/v1/admin/claims`, `/api/v1/admin/conflicts`, `/api/v1/admin/review-tasks`, claim decision, release resolution, publication, and manifest endpoints. This guard is not secure authentication.

Full checks:

```bash
make check
make web-build
make web-e2e
make web-e2e-full-stack
make validate-golden-set
# or all repository gates
make verify
```

Security, operations and deployment state are documented in:

- `docs/SECURITY_REVIEW.md`
- `docs/BACKUP_RESTORE.md`
- `docs/DEPLOYMENT.md`
- `docs/SOURCE_LICENSES/README.md`
- `docs/RELEASE_CHECKLIST.md`

## Senior takeover status

The repository is not a complete MVP. The verified current slice is one
official USGS earthquake profile for `1964-03-27`. See
`docs/SENIOR_TAKEOVER_AUDIT.md` and `docs/RELEASE_CHECKLIST.md` for confirmed
behavior and red release gates.

For a repeatable development reset, reset PostgreSQL and both local immutable
storage roots together:

```bash
make clean-reset
make db-up
make db-migrate
make ingest-usgs-fixture
make review-usgs-fixture
make ingest-un-wpp-fixture
make review-un-wpp-fixture
make ingest-ucdp-annual-fixture
make review-ucdp-annual-fixture
make ingest-ucdp-ged-fixture
make review-ucdp-ged-fixture
make publish-golden
```

Review is deliberately separate from publication. `publish-golden` must fail
when claims remain candidates, review tasks remain open, required checks are not
all passed, or the release lacks a public-display license snapshot.
