# Senior Takeover Audit

Audit date: 2026-07-24

Branch audited: `agent/usgs-earthquake-vertical-slice`

Inherited commit: `51bc1b0de577d861a5baa28fa4e857df9ddfc5c2`

Senior continuation branch: `agent/senior-takeover-mvp`

## Audit method

This audit treats code, migrations, stored artifacts, and commands run during
the takeover as evidence. Documentation is compared with that evidence rather
than accepted as proof.

The audit read every tracked instruction, product and architecture document,
migration, API module, source adapter, pipeline and publication service, API
route, frontend route and component, test, dependency manifest, infrastructure
file, fixture, and tracked lockfile. There is no repository-level instruction
file and no `.github` directory.

## Ground truth

### What the repository actually does

- PostgreSQL/PostGIS migrations `20260723_0001` through `20260724_0007` create
  the foundation schema, evidence snapshot tables, lifecycle triggers, and the
  fields used by the USGS slice.
- The SQLAlchemy model exposes the foundation tables plus
  `raw_source_records`.
- One source adapter retrieves or reads a committed official USGS GeoJSON
  fixture for event `official19640328033616_30`.
- The adapter validates exactly that event, stores an immutable raw object,
  creates one source release, creates nine imported candidate claims, opens
  nine review tasks, and records one pipeline run and quality check.
- A USGS-specific function accepts all nine claims, creates deterministic
  single-source resolutions, creates one earthquake event, one event time, one
  historical Alaska geography version, one event location, and one quality
  assessment.
- A USGS-specific publisher produces a version-addressed profile for
  `1964-03-27`. Only recorded-event and evidence sections contain statements.
- The publication service hashes canonical JSON, snapshots evidence, writes
  through a filesystem storage interface, creates a publication manifest, and
  creates a day-profile row.
- FastAPI serves metadata endpoints, a hash-verified published artifact, and a
  small development-token-guarded admin API.
- Next.js provides date entry, a date route, explicit loading/unpublished/error
  states, seven separate evidence sections, and a public provenance disclosure.
- Ordinary browser rendering does not query USGS.

### What starts successfully

- `make install` succeeds with Node `v20.20.0`, pnpm `10.14.0`, Python
  `3.13.11`, and Docker `29.6.2`.
- `make db-up` starts `postgis/postgis:16-3.5`.
- `make db-migrate` migrates an empty database to `20260724_0007`.
- `make ingest-usgs-fixture` succeeds and produces checksum
  `32beb46bd6d5fd5b06943c08e32f4a83ad4a90a28690bea49c42f3da59210c8b`.
- FastAPI starts and serves `/health` and the golden profile when configured
  with the storage root that contains its manifest object.
- Next development mode starts.
- A manual headless-Chromium check passed through
  browser -> Next proxy -> FastAPI -> manifest -> published JSON and opened the
  provenance view.

### What tests pass

Commands run on 2026-07-24:

| Command | Result |
| --- | --- |
| `make install` | Passed; pnpm warned that three dependency build scripts were ignored |
| `make db-reset && make db-up && make db-migrate` | Passed |
| `make ingest-usgs-fixture` | Passed |
| `make check` | Passed |
| Contract ESLint/typecheck/Vitest | Passed; 1 test |
| Ruff | Passed |
| mypy | Passed; 19 source files |
| pytest | Passed; 55 tests |
| Web ESLint/typecheck/Vitest | Passed; 4 tests |
| `make web-build` | Passed |
| `make web-e2e` | Passed; 2 mocked Playwright tests |
| Manual full-stack Chromium check | Passed in development mode |

The current suite proves many relational invariants and the isolated USGS happy
path. It does not prove the contracted MVP.

### What fails

#### Clean publication after the documented database reset

Command:

```text
make db-reset
make db-up
make db-migrate
make ingest-usgs-fixture
make publish-golden
```

Observed result:

```text
RuntimeError: Published profile content did not match its manifest hash.
```

The database was empty, but
`.local/published-profiles/day/1964-03-27/profile-v1.json` survived. The new
database allocated a new release identity and retrieval timestamp, so the new
profile bytes differed. The filesystem adapter correctly refused to overwrite
immutable version 1. The repository has no coordinated development reset for
database, raw storage, and publication storage.

#### Production frontend start after the documented verification order

`make web-build` succeeds. `make web-e2e` then launches `next dev` against the
same `.next` directory. A subsequent `next start` fails:

```text
Could not find a production build in the '.next' directory.
```

The E2E configuration does not isolate its development build directory from the
production build output. There is also no root Make target for `next start`.

### What is scaffolded but nonfunctional

- People, organizations, aliases, and external identifiers have tables but no
  candidate ingestion, entity resolution, merge records, or editorial flow.
- Metrics, observations, coverage, impacts, and derived values have foundation
  tables but no official demographic or conflict pipeline.
- Source lineage can store relationships, but resolution does not derive
  independence from it.
- Review tasks and admin endpoints exist, but there is no review frontend,
  production authentication, actor audit, allowed-transition service, duplicate
  review, entity merge review, geography review, quality review, preview, or
  correction UI.
- Corrections can be recorded in the service layer, but there is no editorial
  correction workflow or rollback operation.
- Three profile levels exist as constrained values, but only one standard-era
  date is published.

### What the handoff gets right

- The migrations build from an empty PostgreSQL/PostGIS database.
- Source releases and raw source records are protected by database triggers.
- Published manifests, day profiles, and publication evidence are immutable
  after publication.
- Published statement mappings carry immutable evidence snapshots and hashes.
- The USGS fixture is network-independent and its checksum is stable.
- The public API reads a published artifact instead of rebuilding a profile
  through runtime joins.
- Missing casualty data is not converted to zero.
- The frontend visibly separates the seven epistemic sections.
- The development review token is explicitly not represented as secure
  authentication.
- The handoff's listed concerns about caller-provided lineage roots, mocked
  browser tests, the development guard, and unnecessary republishing are real.

### What the handoff gets wrong or overstates

- A new engineer cannot reproduce publication from the documented reset path
  when prior local artifacts exist.
- The phrase "full local verification" overstates two Playwright tests that
  intercept the Next API route and never exercise FastAPI, PostgreSQL, manifest
  verification, or the real published artifact.
- The public provenance chain is not complete for composite statements.
  Event identity also displays the title, event time also displays the local
  civil date, and location also displays named geography, but each statement is
  mapped to only one resolved claim.
- Publishing is not editorially separated from acceptance and resolution.
  `publish_golden_profile` invokes `accept_and_resolve_release`, which changes
  candidate state and closes review tasks as part of publication.
- The admin publication endpoint is a golden-date-specific operation, not a
  defensible general review/publish surface.
- "No partial publication after failed validation" is only proven for the
  adapter's single validation failure. Publication does not enforce a general
  pipeline success, required-check set, quality threshold, or license gate.
- Documentation references nonexistent
  `services/api/app/publication/service.py` and
  `services/api/tests/test_foundation.py`.
- The handoff's baseline commit is stale relative to the audited commit.

### Documentation claims without implementation evidence

- No implementation supports UN demographic context.
- No implementation supports UCDP annual or event-level conflict context.
- No implementation supports Wikidata/Wikimedia candidate ingestion.
- No implementation supports an apocalypse catalog.
- No implementation supports a wonder and progress catalog.
- No model card or comparison cohort implementation exists.
- No reviewed golden set of 100 dates exists.
- No profile exists in the limited or enhanced era.
- No source release has the required license snapshot and permission fields.
- No production authentication exists.
- No deployment, backup/restore, structured logging, security review,
  dependency review, license inventory, or production-readiness evidence exists.
- No CI configuration exists.

## Contract reconciliation

### Phase A: Foundation repair

| Requirement | State | Evidence |
| --- | --- | --- |
| Foundation tables and constrained values | Partially implemented | Migrations 0001-0007 and `app/models.py` |
| Claims as atomic units | Partially implemented | Claims and resolutions exist; composite statements escape one-root completeness |
| Resolved-claim versioning | Implemented correctly for explicit service calls | `resolve_claim` and database unique constraints |
| Source-release immutability | Implemented correctly | Database trigger and integration test |
| Source lineage | Partially implemented | Storage exists; independence is caller-declared |
| Historical geography | Partially implemented | Versioned geography and overlap guard; only one assignment |
| Missingness | Implemented correctly in foundation schema | Observation, coverage, impact, and derived-value checks |
| Quality assessments | Partially implemented | One hardcoded USGS rule; no reusable grade contract or review workflow |
| Methodologies | Partially implemented | Versioned rows exist; code version remains slice-specific |
| Publication manifests | Partially implemented | Hashing/snapshots work; no general release gate or rollback |
| Published-object immutability | Implemented correctly | Filesystem refusal plus relational triggers |
| Pipeline runs and checks | Partially implemented | USGS records them; dry-run does not; publication gate is incomplete |
| Corrections | Partially implemented | Append-only service exists; no usable workflow |
| API/frontend boundaries | Partially implemented | Public artifact path is correct; admin and full-stack tests are incomplete |
| Release-level licensing | Missing | Only coarse legal-review enum fields exist |

### Phase B: Source adapter framework

Partially implemented. `SourceAdapter` is a USGS-shaped protocol inside
`app/usgs.py`; orchestration, release registration, failure handling, and run
recording are not reusable framework services. Fixture and dry-run modes exist,
but dry-run does not record a pipeline run. Record hashes use the entire release
checksum, which is only record-specific because the current release contains
one record.

### Phase C: Data pipelines

| Pipeline | State |
| --- | --- |
| USGS | Partially implemented and runnable |
| UN demographic context | Missing |
| UCDP conflict context | Missing |
| Wikidata/Wikimedia candidates | Missing |
| Curated apocalypse catalog | Missing |
| Wonder and progress catalog | Missing |

### Phase D: Comparison system

Missing. The `comparability_status` field is not a comparison model. There are
no cohorts, model versions, transformations, coverage rules, cohort hashes,
percentiles, uncertainty, model cards, or public comparison statements.

### Phase E: Editorial and review system

Partially implemented at the API/database level and missing at the user
interface/security level. Current publishing can bypass genuine review by
accepting candidates itself.

### Phase F: Golden set

Missing. One date is a vertical-slice fixture; no version-controlled 100-date
selection, rationale, validation matrix, manual-review status, or cross-era
publication exists.

### Phase G: Public application

Partially implemented. Date search, invalid/unpublished/loading/error states,
seven sections, source attribution, and one provenance control work. Date
navigation, canonical metadata, comparison cohorts, disagreement detail,
metric-level availability, accessibility checks, and cross-era content do not.

### Phase H: Publication and runtime

Partially implemented. Hash verification, immutable version objects, corruption
handling, and append-only republishing work. Coordinated reset, caching policy,
rollback, generic safe-publication gates, license enforcement, and recovery
from database/storage divergence do not.

### Phase I: Quality and operations

Partially implemented. Local installation, migrations, fixture mode, linting,
typing, unit/integration tests, a mocked browser suite, and build commands
exist. CI, real full-stack acceptance, structured logs, security review,
dependency review, license inventory, backup/restore, deployment documentation,
and a production-readiness checklist are missing.

## Epistemic-contract violations

### Composite statements have incomplete evidence roots

Actual cause:

The profile builder models a statement as one path with exactly one
`PublicationStatementEvidenceInput`, while several displayed statements combine
values from multiple resolved claims.

Why this is wrong:

A user can see a title, local date, timezone interpretation, or named geography
that is not present in the one resolved claim linked to that statement path.
The immutable snapshot is internally valid but incomplete for the public bytes.

Correct fix:

Make each independently factual published statement atomic, or permit an
explicit multi-root statement mapping with complete immutable snapshots. The
smaller and safer correction for the current profile is to split statements so
each path has one factual root and represent transformations such as local civil
time as derived values with explicit inputs and methodology.

Must not change:

Do not weaken the rule that every displayed factual statement has durable
evidence. Do not hide combined facts in `details`.

Verification:

Add a profile-schema traversal test that compares every factual field with its
declared roots, then trace each golden artifact statement back to the relevant
raw record and methodology.

### Quality explanation is attached to event identity evidence

Actual cause:

The public quality statement maps to the event-identity resolution even though
its content is produced by a quality methodology.

Why this is wrong:

The identity claim does not support the grade or its explanation.

Correct fix:

Represent the quality conclusion as a derived value or a resolved editorial
claim with the quality dimensions, source inputs, method version, and explicit
single-source consequence.

Must not change:

Do not replace the explanation with an opaque score.

Verification:

The evidence snapshot for the quality statement must have the quality
methodology and all assessed evidence as its direct provenance.

### Source independence is caller-declared

Actual cause:

`EvidenceCandidate.lineage_root` is supplied by the caller and
`deterministic_resolution` counts distinct strings.

Why this is wrong:

A copied or derived release can be mislabeled as independent confirmation.

Correct fix:

Derive independence groups from persisted `source_lineage`, conservatively
treat unknown relationships, detect cycles, and make the resulting grouping
part of the versioned resolution rationale.

Must not change:

Do not add a weighted truth score or infer independence from publisher names.

Verification:

Integration tests must build independent, republished, derived, and cyclic
lineage graphs in PostgreSQL and assert the resolution grouping.

### Publication performs review decisions

Actual cause:

`publish_golden_profile` calls `accept_and_resolve_release`, and that function
accepts every claim and resolves all open review tasks.

Why this is wrong:

Publication can transform unreviewed candidates into accepted facts instead of
requiring completed review and resolution.

Correct fix:

Separate import, review decision, resolution, editorial selection, preview, and
publication services. Publication must be read-only with respect to claims,
review tasks, resolutions, and quality assessments, except for append-only
publication records.

Must not change:

Do not remove fixture convenience; provide an explicit development workflow
that performs each transition and records it.

Verification:

Publishing with candidate claims or open blocking tasks must fail without
changing their state. A reviewed fixture workflow must succeed.

## Foundational operational defects

### Database and artifact reset are not coordinated

Actual cause:

`make db-reset` removes only PostgreSQL state. Version numbers are database
allocated, while immutable objects remain in `.local/published-profiles`.

Why the behavior is wrong:

The documented clean path cannot be repeated in a previously used checkout.

Correct fix:

Add an explicit development-only clean-reset command that validates configured
paths are inside the repository's `.local` directory, removes database and local
raw/publication state together, and documents the destructive scope. Production
retention behavior remains append-only.

Must not change:

Do not make normal publication overwrite an existing versioned object.

Verification:

Run the complete clean path twice from separately reset local states and publish
version 1 successfully both times. Run a normal republish without reset and
prove it creates version 2.

### E2E and production builds share `.next`

Actual cause:

Playwright starts `next dev` in the application directory after `next build`.

Why the behavior is wrong:

Verification destroys the artifact required by `next start`.

Correct fix:

Use an isolated Next build directory for browser tests or run browser tests
against a production build. Prefer a real full-stack test server using a
disposable database and publication root.

Must not change:

Do not weaken browser assertions or replace the hash-verified API path with a
frontend fixture.

Verification:

`make verify` followed by the documented production start command must work.

### Publication gates are adapter-specific and incomplete

Actual cause:

The golden publisher searches only for a failed quality check associated with
one pipeline run. It does not require a succeeded run, a declared required-check
set, approved public-display licensing, completed review, or a valid selection.

Why the behavior is wrong:

Warnings, absent checks, unresolved licensing, or unreviewed claims can publish.

Correct fix:

Create a reusable publication eligibility service that evaluates immutable
release licensing, pipeline state, required checks, claim/review state,
resolution state, quality assessment, editorial selection, and artifact schema
before any storage write.

Must not change:

Do not query external sources at publication or render time.

Verification:

One focused failure-path test per gate must prove zero manifests, zero profiles,
and zero objects are created.

### Filesystem write and database commit are not atomic

Actual cause:

The publication service writes the object before the caller commits the
database transaction.

Why the behavior is wrong:

A later database failure can leave an unreferenced immutable object that blocks
future reuse of the same version path.

Correct fix:

Introduce staged objects and an explicit finalize protocol, or write to a
content-addressed staging location and promote only after durable manifest
state. Add orphan detection and safe development cleanup.

Must not change:

Do not mutate a finalized versioned object.

Verification:

Inject a database failure after staging and prove no finalized version object
exists; inject a finalize failure and prove no manifest is advertised as
published.

### License eligibility is not modeled

Actual cause:

Sources, releases, methodologies, and assessments only carry a coarse
`legal_review_status`.

Why the behavior is wrong:

The required commercial-use, redistribution, derivatives, attribution,
public-display, raw-download, terms-snapshot, and terms-check facts cannot be
stored or enforced.

Correct fix:

Add immutable release-level license snapshots and explicit permission fields.
Publication eligibility must require affirmative public-display eligibility and
honor raw-redistribution restrictions separately.

Must not change:

Do not encode legal conclusions that have not been reviewed. Keep EM-DAT
excluded.

Verification:

Approved, pending, restricted, and rejected license fixtures must produce the
expected publication decisions without exposing restricted raw payloads.

## Implementation contract

### Immediate correction phase

This phase will:

- Add immutable release-level licensing records and publication eligibility.
- Separate review, resolution, selection, and publication.
- derive source independence from stored lineage.
- Repair atomic public provenance and quality provenance.
- Add staged/finalized local publication behavior and a safe coordinated local
  reset.
- Isolate or replace the mocked browser path with a real full-stack acceptance
  path.
- Add CI and the required release, model-card, source-license, security, and
  operations records.

This phase will not:

- Claim legal approval without a recorded source snapshot.
- Add EM-DAT or GDELT.
- Add a universal historical badness score.
- Query third-party sources during ordinary page rendering.
- Generate all public dates before the reviewed golden 100 is valid.

Acceptance criteria:

- Existing foundation invariants remain green.
- Every golden USGS public field has complete direct provenance.
- Publication cannot modify candidate/review/resolution state.
- Publication cannot proceed after failed or absent required checks, unresolved
  licensing, open blockers, unresolved claims, or failed artifact validation.
- A database failure cannot leave a finalized orphan at a version path.
- The documented clean path is repeatable.
- A real browser test reaches the actual API and published artifact.

Verification commands will be added to `Makefile` and `README.md` as executable
targets rather than pseudocode.

### MVP expansion dependency order

1. Complete Phase A and the reusable Phase B contract.
2. Register and enforce source-license snapshots before public data expansion.
3. Implement official demographic annual observations and derived daily
   equivalents with exact annual-context language.
4. Implement legally eligible UCDP fixtures and coverage semantics, or mark the
   pipeline externally blocked without publication.
5. Implement offline candidate ingestion and editorial entity/event review.
6. Implement curated apocalypse and wonder/progress catalogs with explicit
   coverage grades.
7. Implement only evidence-backed period comparisons and model cards.
8. Create, review, validate, and publish the golden 100 with at least one
   profile in each era.
9. Complete the review and public interfaces.
10. Complete CI, accessibility, security, dependency, backup/restore,
    deployment, and release-gate evidence.

## Current release decision

The repository is not an MVP and is not technically release-ready.

The USGS vertical slice is useful, but the release gates are red for clean
reproduction, complete provenance, licensing, pipeline coverage, golden-set
coverage, review separation, production authentication, CI, full-stack
acceptance enrollment, accessibility, security, operations, and deployment.
