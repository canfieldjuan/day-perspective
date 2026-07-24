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
