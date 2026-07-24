# Foundation Handoff

## Repository state

This handoff begins during the foundation phase. `docs/STATUS.md` is the source
of truth for the current implementation and verification state. The repository
root is `/home/juan-canfield/Desktop/day-perspective`.

Current Alembic head: `20260723_0004` in
`services/api/alembic/versions/20260723_0004_lifecycle_integrity.py`. The
earlier exploratory file with that revision number was never applied; the live
database advanced directly from `20260723_0003` to this final revision.

The foundation is claim-first and offline-only. It intentionally contains no
production historical dataset and must return an honest unpublished state when
no immutable profile artifact exists.

## Architecture map

| Path | Purpose |
| --- | --- |
| `apps/web` | Next.js date input and separate evidence sections. |
| `services/api` | FastAPI, SQLAlchemy models, Alembic migration, offline pipeline entry points, profile storage interface. |
| `packages/contracts` | Shared TypeScript API/profile contracts. |
| `data/fixtures` | Clearly test-only inputs and seeds. |
| `docs/PRODUCT_CONTRACT.md` | Public scope, bands, distinctions, deferrals. |
| `docs/ARCHITECTURE.md` | Layers, boundaries, offline ingestion, runtime path. |
| `docs/CLAIM_LIFECYCLE.md` | Resolution, disagreement, correction, immutable publication. |
| `docs/DATA_DICTIONARY.md` | Schema vocabulary, constraints, relationships, deletion rules. |
| `docs/DECISIONS.md` | Decisions and revisit triggers. |
| `infra`, `docker-compose.yml`, `Makefile`, `scripts` | Local PostGIS and operational commands. |

## Core trace and model

```text
published statement
-> publication statement evidence
-> resolved claim or derived value
-> claim/observation input
-> source release
-> raw artifact
-> methodology and code version
-> manifest hash and immutable JSON
```

The initial model includes the required tables plus three necessary provenance
joins: `resolved_claim_evidence` for supporting/dissenting claim links,
`derived_value_inputs` for calculation inputs, and
`publication_statement_evidence` for published-statement provenance.

```text
sources, source_releases, source_lineage, claims, resolved_claims,
resolved_claim_evidence, events, event_times, geographies, geography_versions,
event_locations, people, organizations, entity_aliases, external_identifiers,
metrics, observations, event_impacts, metric_coverage, quality_assessments,
methodologies, derived_values, derived_value_inputs, publication_manifests,
publication_statement_evidence, day_profiles, pipeline_runs, quality_checks,
review_tasks, corrections
```

Source releases and published manifests are immutable. Corrections append new
claims/resolutions/artifacts/manifests/profile versions and link to prior
versions of the same date and profile type; PostgreSQL also rejects direct
cross-date/type manifest supersession and successor forks. They never overwrite
published history.

Each published JSON statement path also has one immutable
`publication_statement_evidence` mapping to a resolved claim or derived value.
The publication service rejects a profile whose mappings do not cover every
statement path.

## Runtime behavior

```text
web date shell -> GET /api/v1/day/{yyyy-mm-dd}
-> PostgreSQL profile/manifest metadata -> immutable JSON storage
-> validated profile or profile_not_published
```

The browser and ordinary API path never call a third-party historical source.
Public dates are 1900-2025, banded as `limited_historical` (1900-1949), `standard_statistical`
(1950-1988), and `enhanced_structured` (1989-2025). A band is not a promise that a date
has published content.

The UI must keep Recorded on this date, Typical day in this year, Wider
historical context, Curated claims, Derived comparisons, Wonder and progress,
and Evidence notes distinct. Daily equivalents and period context must not look
like direct observations.

## Operational commands

The README and Makefile are operational authority. The intended commands are:

```bash
corepack pnpm install --frozen-lockfile
docker compose up -d db
make api-migrate
make api-seed
make api-run
corepack pnpm --filter @day-perspective/web dev
```

Expected checks are:

```bash
make check
make web-e2e
make web-build
```

Do not claim any command passed unless current results are recorded in
`docs/STATUS.md`.

## Decisions and deliberate deferrals

See `docs/DECISIONS.md` for alternatives and revisit triggers. The key choices
are claim-first provenance, immutable releases/manifests, PostgreSQL/PostGIS,
local immutable JSON artifacts, explicit uncertainty, and no live source calls.

Deferred: full GDELT, EM-DAT, Wikidata, UCDP, and UN ingestion; universal
hardship scoring; full ranking; user/social features; ancient history;
AI-generated facts; runtime third-party queries; production deployment; all
46,000 profiles; queue, vector, and graph infrastructure.

## Risks for senior review

- Local profile storage lacks production durability, backup, authorization, and
  concurrent-writer guarantees.
- The relational model is intentionally provenance-rich but needs exercise with
  a real licensed source release.
- Local PostGIS migration and trigger verification have passed; repeat them in
  every clean environment and against the first real source workflow.
- Typed target references for aliases, identifiers, quality, and review need
  continued referential-integrity scrutiny as entity types grow.
- Profile bands are product contracts, not a coverage audit.
- Before the first real published profile, senior engineering should decide
  whether post-publication provenance roots are frozen transitively or captured
  as immutable root snapshots; this foundation freezes releases, manifests,
  statement mappings, and profile artifacts but intentionally does not yet
  choose one of those broader lifecycle policies.
- Do not fill sparse pages with fixture data, AI-generated facts, or silent
  runtime source queries.

## Recommended next task

Select one bounded, licensed source release and build an end-to-end offline
vertical slice: raw registration, claim normalization, review/resolution, one
methodology-bound derived value, one immutable published profile, and a
provenance-first page render. Repeat migration and local-command verification
against a clean PostgreSQL/PostGIS environment before beginning that slice.
