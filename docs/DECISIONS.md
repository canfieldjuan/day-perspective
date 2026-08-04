# Architecture Decisions

## D001: Claims are the atomic unit

**Decision:** Claims, not raw records or event rows, are the provenance root.

**Context:** Historical sources can disagree, be corrected, and state time at
different precision.

**Alternatives considered:** Treat imported records as truth; event-centric
truth tables; free-form editorial pages.

**Reason:** Claim resolution makes evidence, disagreement, supersession, and
method explicit and testable.

**Consequences:** Canonical events/entities require resolved-claim links and
published statements need evidence paths.

**Revisit trigger:** Only if real ingestion exposes a source assertion that
cannot be represented without weakening provenance.

## D002: Releases and manifests are immutable

**Decision:** Ingested source releases and published profile manifests are
append-only. Corrections create new versions.

**Context:** Reproducibility requires knowing exact raw material, method, code,
and profile bytes at publication time.

**Alternatives considered:** In-place updates; current-profile pointers without
history; unhashed artifacts.

**Reason:** Immutability preserves the required published-statement-to-raw
trace and makes corrections honest.

**Consequences:** The model needs supersession links, hashes, and retained
storage.

**Revisit trigger:** Review storage retention after a production legal and
durability plan exists; do not relax audit immutability.

## D003: PostgreSQL with PostGIS is authoritative

**Decision:** Use PostgreSQL/PostGIS with SQLAlchemy and Alembic.

**Context:** The foundation needs constraints, transactions, migrations, joins,
date ranges, and historical geography without premature infrastructure.

**Alternatives considered:** Document storage; graph database; vector database;
browser-held JSON.

**Reason:** Relational constraints enforce lifecycle invariants and PostGIS
supports geographically versioned history.

**Consequences:** Some migration guards are intentionally PostgreSQL-specific.

**Revisit trigger:** Add specialized stores only for measured needs while
retaining relational provenance authority.

## D004: Publish immutable JSON through a storage interface

**Decision:** Build day-profile JSON offline and serve it through a storage
interface with local filesystem storage in development.

**Context:** Profiles need stable fast reads while the relational store retains
structured evidence and publication state.

**Alternatives considered:** Assemble profiles per request; only database blobs;
cloud storage coupled into the first foundation.

**Reason:** Manifest-hashed artifacts are reproducible, inspectable, and
portable to later durable storage.

**Consequences:** Local storage is not production-grade and unpublished dates
are a valid outcome.

**Revisit trigger:** Replace local storage when deployment requires durability,
backups, authorization, or concurrent writers.

## D005: No live third-party render-time calls

**Decision:** Browser and ordinary API requests read local metadata/artifacts
only; third-party acquisition is offline.

**Context:** Live calls can change history silently and make sources,
availability, licensing, and reproduction unstable.

**Alternatives considered:** Browser vendor calls; API fan-out on page load;
cache-miss refreshes.

**Reason:** The source-release boundary is required for reliable provenance.

**Consequences:** Fresh data needs a pipeline run and a date may be unpublished.

**Revisit trigger:** None for normal rendering. New providers are offline
adapters only.

## D006: Preserve temporal and data distinctions

**Decision:** Model temporal precision, temporal assignment, date role, data
status, publication status, missing reason, and comparability separately.

**Context:** Historical date products often conflate annual allocation,
reporting date, direct observation, absence, and zero.

**Alternatives considered:** One date field; null-as-zero; implicit frontend
labels.

**Reason:** These distinctions prevent false date-specific or overconfident
presentation.

**Consequences:** Schema and UI carry more metadata and evidence notes.

**Revisit trigger:** Extend calendars/interval handling for real source needs;
do not collapse current dimensions.

## D007: Profile bands are evidence contracts

**Decision:** Public shell is 1900-2025, with `limited_historical` 1900-1949, `standard_statistical`
1950-1988, and `enhanced_structured` 1989-2025.

**Context:** Coverage and structured data vary by period.

**Alternatives considered:** One uniform profile; unbounded history; automatic
content for every date.

**Reason:** Bands communicate responsible support while preserving an honest
unpublished state.

**Consequences:** No profile type promises populated content.

**Revisit trigger:** Change bands only after documented coverage evaluation and
product approval.

## D008: Keep the first phase narrow

**Decision:** Use the requested monorepo stack and defer broad ingestion,
ranking, accounts, social features, queues, graph/vector databases, and
deployment.

**Context:** This is a transfer-ready foundation, not a complete product.

**Alternatives considered:** Broad demo first; fake data to fill pages; early
distributed infrastructure.

**Reason:** A small vertical skeleton makes architecture inspectable and avoids
premature commitments.

**Consequences:** Endpoints may correctly be metadata-only/unpublished and
fixtures are test-only.

**Revisit trigger:** Add scope only when an evidence-backed product slice or
measured operational requirement justifies it.

## D009: Make published statements relationally traceable

**Decision:** Store one immutable `publication_statement_evidence` row for
each published JSON statement path, targeting exactly one resolved claim or
derived value.

**Context:** A manifest hash proves profile bytes but cannot, by itself, prove
which provenance root supports an individual sentence in those bytes.

**Alternatives considered:** Treat profile JSON as self-provenancing; use only
free-form IDs inside JSON; resolve provenance at render time.

**Reason:** A relational, immutable mapping makes the required statement to
claim/derivation trace queryable and prevents opaque published claims.

**Consequences:** Publication builders must supply statement-path mappings;
published mappings cannot be edited, deleted, or retargeted in place. A derived
statement also needs a resolved-claim link or durable observation/claim input
lineage before it can publish. Deferred parent checks cover deletes and
retargeting updates, and profile supersession links retain a single date/type
identity.

**Revisit trigger:** Extend the mapping only if a real profile schema needs an
additional provenance-root category without weakening the one-root rule.

## D010: Keep correction history linear

**Decision:** A manifest or day profile can have at most one direct successor,
and a successor must retain its predecessor's date and profile type.

**Context:** A correction is a new immutable version, not a competing branch
whose currentness would need an unmodeled selection rule.

**Alternatives considered:** Permit correction branches; select the largest
version at read time; keep a mutable current-version pointer.

**Reason:** A single successor constraint makes correction history inspectable,
prevents ambiguous published versions, and preserves the append-only chain.

**Consequences:** A later correction supersedes the latest successor rather
than returning to an earlier version. An exact retry returns the existing
correction, while the same pair with a different rationale is rejected.
Branching would require an explicit canonical-successor product rule before
being enabled.

**Revisit trigger:** Revisit only if editorial policy requires intentional
parallel correction branches and a public selection rule is approved.

## D011: Snapshot publication evidence instead of freezing the working graph

**Decision:** Each publication-statement mapping captures canonical JSON for
its complete resolved-claim or derived-value evidence root and stores its
SHA-256 hash. The manifest source-snapshot hash is calculated from ordered
statement paths and evidence hashes.

**Context:** Source releases, manifests, profile artifacts, and statement
mappings were immutable, but imported and resolved claims remained editable.
A mapping could therefore keep the same identifier while its live evidence
changed, making an earlier publication impossible to reconstruct exactly.

**Alternatives considered:** Freeze every transitively referenced claim,
resolution, evidence edge, observation, and methodology after publication;
trust mutable rows plus identifiers; copy only the final resolved value; allow
callers to provide an opaque source-snapshot hash.

**Reason:** A canonical snapshot preserves what was published without making
the working evidence graph unusable for review and correction. Capturing the
complete root preserves dissent, missingness, source checksums, record locators,
method versions, derived inputs, metric definitions, historical geography,
quality assessments, pipeline configuration, and recursive source lineage
rather than only the final sentence.

**Consequences:** Publication fails if any evidence root is incomplete. Snapshot
JSON is intentionally redundant with normalized tables. Schema changes require
explicit snapshot-schema versioning. Migration `20260723_0005` refuses silent
backfill when publication evidence already exists. Methodology quality targets
use a dedicated `target_methodology_id`; the existing `methodology_id` continues
to identify the method used to perform an assessment.

**Revisit trigger:** Introduce a separately versioned archival snapshot store
only when measured profile volume or retention requirements make relational
JSONB unsuitable; never weaken historical reconstructability.

## D012: Use the official USGS FDSN summary record as the first adapter release

**Decision:** Pin the official date-range GeoJSON response containing `official19640328033616_30` as the minimal network-independent fixture, while retaining an explicit live offline retrieval command.

**Context:** The event detail product is much larger and changes as USGS product bundles are updated. The summary record contains every predicate supported in this slice.

**Alternatives considered:** Commit the full detail product; hardcode selected values; query USGS at render time.

**Reason:** Exact raw bytes remain reproducible without network tests, while the adapter still validates the official response schema and record identity.

**Consequences:** Casualties are unsupported and omitted. A changed live response creates a new content-sensitive release.

**Revisit trigger:** A required predicate exists only in a detail product or a separate authoritative source.

## D013: Separate UTC occurrence from historical local civil-date assignment

**Decision:** Store the UTC instant, IANA timezone, instant-specific offset, local date, and interpretation separately.

**Context:** The earthquake occurred on March 28 UTC but belongs to the March 27 public profile in Alaska civil time.

**Alternatives considered:** Store only UTC date; store a fixed offset without a timezone rule; place local date inside prose.

**Reason:** This keeps temporal precision distinct from product date assignment and makes the interpretation inspectable.

**Consequences:** Historical timezone data becomes a reproducibility dependency and is surfaced in provenance.

**Revisit trigger:** Events require disputed or jurisdiction-specific calendar assignment.

## D014: Version-address public profile objects while retaining evidence hashes

**Decision:** Publish local objects at `day/{date}/profile-v{n}.json`; use content hashes for integrity and manifest evidence snapshots for provenance.

**Context:** Consumers need a stable version locator, while corrections must never rewrite version 1.

**Alternatives considered:** Content-hash-only filenames; mutable `latest.json`; database reconstruction at request time.

**Reason:** Version paths express publication history clearly and content hashes still detect corruption.

**Consequences:** Identical republishing can create a new version if explicitly triggered; orchestration should avoid unnecessary republishes operationally.

**Revisit trigger:** A remote object store needs atomic aliases or retention policies.

## D015: Use an explicit development review guard, not simulated authentication

**Decision:** Guard `/api/v1/admin/` with `X-Development-Review-Token` and label it development-only everywhere.

**Context:** The slice must prove review actions, while production identity and authorization are out of scope.

**Alternatives considered:** Unguarded endpoints; fake login UI; full production authentication.

**Reason:** The guard prevents accidental casual use locally without misrepresenting security.

**Consequences:** Admin endpoints must never be exposed as production-ready.

**Revisit trigger:** Any deployment or multi-user review workflow.

## D016: Gate publication with immutable release-level license records

**Decision:** Every public source release must have an immutable
`source_release_licenses` row that explicitly records commercial use,
redistribution, derivatives, attribution, public display, raw download, terms
date and legal status.

**Context:** Source-level legal status was too coarse and could not prove the
terms that applied to an immutable release.

**Alternatives considered:** Free-text source notes; one license per source;
publication-time configuration.

**Reason:** Release-level snapshots make the publication decision reproducible
when upstream terms later change.

**Consequences:** Missing or restricted license records block publication.
Machine-readable standard licenses do not replace human launch approval.

**Revisit trigger:** Human counsel requires a separate approval workflow or
license inheritance policy.

## D017: Separate review decisions, resolution and editorial selection

**Decision:** Record accept/reject actions in an append-only ledger; resolution
requires already accepted claims; publication requires explicit editorial
selection.

**Context:** The inherited admin resolve action could accept candidates as a
side effect and claim status could be mutated without a decision record.

**Alternatives considered:** Direct claim status mutation; resolution as
implicit acceptance; publication-time auto-review.

**Reason:** Each epistemic transition must be explainable and attributable.

**Consequences:** Development fixture commands perform an explicit review step
before resolution. The admin resolution endpoint rejects candidates.

**Revisit trigger:** Product defines multi-reviewer quorum or appeal workflows.

## D018: Model annual conflict aggregates as period context

**Decision:** Add `period_context` to temporal assignment and use it for the
UCDP 1964 conflict-year count.

**Context:** `direct_record`, `uniform_period_allocation` and
`editorial_context` each misdescribe an aggregate count over a calendar year.

**Alternatives considered:** Reuse an existing enum; store the distinction only
in prose.

**Reason:** The distinction must survive database, artifact and UI boundaries.

**Consequences:** Public language states that the count describes the year, not
the selected date.

**Revisit trigger:** A broader typed derivation taxonomy replaces temporal
assignment.

## D019: Treat Wikidata as candidate discovery, not confirmation

**Decision:** Pinned Wikidata entity JSON creates candidate claims and review
tasks only.

**Context:** Q749610 includes values without displayed references, including a
fatality count. Import success is not evidence acceptance, and Wikipedia-derived
claims are not independent of their republishers.

**Alternatives considered:** Automatic event merge; use Wikidata as second
source; exclude it entirely.

**Reason:** Candidate discovery is useful while preserving the claim-first
review contract.

**Consequences:** The adapter creates no resolved claim, canonical event or
public statement.

**Revisit trigger:** Entity-resolution and reference-quality workflows are
implemented.

## D020: Separate Golden 100 selection from review and publication

**Decision:** Store 100 candidate dates with selection rationale, manual-review
status and publication status as separate fields.

**Context:** A version-controlled list is required, but automated selection
cannot honestly claim human editorial review.

**Alternatives considered:** Mark generated candidates reviewed; defer the file;
publish empty profiles.

**Reason:** Release dashboards must remain red until the real gate is met.

**Consequences:** The validator proves count, era and stress-category coverage
while reporting zero reviewed and zero published.

**Revisit trigger:** Editorial review begins.

## D021: Resolve dependency advisories at framework and lockfile boundaries

**Decision:** Upgrade maintained Next/FastAPI toolchains and pin vulnerable
transitives through lockfiles rather than suppressing audits.

**Context:** Playwright, Sharp/libvips, PostCSS, pytest and Starlette advisories
were present in inherited pins.

**Alternatives considered:** Audit ignores; direct incompatible transitive
pins; document accepted risk.

**Reason:** Vulnerability-free dependency resolution is a release gate.

**Consequences:** Next 16 required native flat ESLint configuration and exposed
an existing React effect issue that was corrected.

**Revisit trigger:** A new audit finding or framework compatibility issue.

## D022: Make editorial review transitions append-only and explicitly ordered

**Decision:** Store every changed editorial decision as a new row with a
monotonic `decision_version`, and use only the latest version for publication
eligibility.

**Context:** The original one-row uniqueness rule made a deferred or rejected
root impossible to select later. Ordering replacement rows by transaction
timestamps would be ambiguous because PostgreSQL `now()` is stable within a
transaction.

**Alternatives considered:** Mutate the original row; order by timestamps and
UUIDs; maintain only the latest state.

**Reason:** Editorial history is evidence and must remain reconstructable with
deterministic ordering.

**Consequences:** Migration `0010` removes one-shot indexes, adds
`decision_version`, and blocks unsafe downgrade when history exists.

**Revisit trigger:** A generalized workflow ledger replaces editorial
selections.

## D023: Couple local publication artifacts to transaction rollback

**Decision:** Stage a versioned artifact, finalize it for pre-commit inspection,
and register ownership with the SQLAlchemy session so rollback or commit failure
removes only the artifact created by that transaction.

**Context:** Writing the immutable final object before the database commit could
leave an orphan that blocked a retry at the same version.

**Alternatives considered:** Finalize only after commit; overwrite conflicts;
store artifacts inside PostgreSQL.

**Reason:** Existing publication callers inspect the artifact before commit,
while retries must not inherit failed transaction output.

**Consequences:** Ordinary rollback and commit-failure paths are clean and
retry version 1 successfully. A process crash between object finalization and
database commit remains a documented local-storage limitation; an unreferenced
version is recoverable on retry.

**Revisit trigger:** Production object storage provides a durable
prepare/finalize protocol or transactional outbox.

## D024: Scope editorial publication authority to section and evidence root

**Decision:** Publication eligibility evaluates the latest decision for each
date, section, root type and root identifier. Publishers declare required
resolved and derived roots grouped by their actual public section.

**Context:** A root can be appropriate for evidence notes while being rejected
from the recorded-event section. Collapsing those decisions by root alone lets
one section silently authorize another.

**Alternatives considered:** Treat selection as profile-wide; infer sections
from root types.

**Reason:** Section placement is an epistemic claim and is already explicit in
the editorial ledger and published artifact.

**Consequences:** Every publication builder must declare the exact section for
each statement root, and cross-section approval substitution fails closed.

**Revisit trigger:** A richer editorial policy engine replaces direct section
selection without weakening section-level authority.

## D025: Serialize editorial versions and terminate review work atomically

**Decision:** Editorial writers take a transaction-scoped advisory lock for
the date/section/root identity before assigning the next version, while partial
unique indexes enforce one row per version. Accepting or rejecting a claim
closes its active review task in the same governance transaction, and claim IDs
are parsed by FastAPI before database access.

**Context:** Read-then-increment alone races for both first and later
decisions; terminal claims otherwise leave unactionable queue entries; raw
strings can reach PostgreSQL UUID operators.

**Alternatives considered:** Rely only on a unique-index retry; lock only an
existing latest row; catch DBAPI errors in the route.

**Reason:** The service must serialize even the first decision, the database
must remain a final backstop, and malformed identifiers should never reach
persistence.

**Consequences:** Competing writers wait rather than create ambiguous latest
versions, direct duplicate inserts fail, and review/API state remains
consistent.

**Revisit trigger:** A distributed editorial command service owns sequencing
with equivalent database guarantees.

## D026: Preserve the WPP estimate-to-projection boundary

**Decision:** The UN WPP adapter imports World aggregate rows for every year
from 1950 through 2025 from the pinned `GEN/01/REV1` release. Years 1950–2023
come from `Estimates` and use `estimated`; years 2024–2025 come from the
`Medium variant` and use `modeled`. Tests use a normalized attributed fixture
extracted from the pinned workbook; live ingestion stores the exact workbook.

**Context:** The product shell requires demographic context through 2025, but
WPP 2024 stops historical estimates at 2023. Treating the medium projection as
an estimate would conceal a material evidence distinction.

**Alternatives considered:** Stop demographic coverage at 2023; label all rows
as estimates; query the WPP API during page rendering; commit the 26 MB
workbook as a test fixture.

**Reason:** Explicit status preserves the epistemic contract while a normalized
fixture keeps tests fast, reviewable and network-independent.

**Consequences:** Public profile builders must display projection language for
2024–2025, parser validation must cover both workbook sheets, and source
revision tests must preserve immutable earlier releases.

**Revisit trigger:** A later official WPP release reclassifies the supported
years or product policy excludes projected annual context.

## D027: UI-arc CSS architecture — split global files plus CSS Modules

**Decision:** The single `apps/web/app/globals.css` is split by concern into
`app/styles/tokens.css` (design-token source of truth), `base.css`,
`landing.css`, `profile.css`, and `admin.css`, imported in that order from
`app/layout.tsx`. Existing UI keeps global class names. New components built
in the Time-Travel arc use co-located CSS Modules; shared visual vocabulary
(evidence chips, stratum rules, seams) lives in `profile.css`. No CSS
tooling, framework, or dependency is added.

**Context:** Eight serial UI slices (`docs/UI_UX_CONTRACT.md` C-14) would
otherwise contend on one 339-line file, and per-component styles need
isolation without a build-system change.

**Alternatives considered:** One growing global file; CSS Modules for
everything including a rewrite of existing selectors; Tailwind or another
utility framework; CSS-in-JS.

**Reason:** The split is a pure move verified line-for-line, keeps existing
e2e/component selectors intact, and Next.js supports both multiple global
imports and CSS Modules natively — zero new dependencies, minimal churn.

**Consequences:** Import order in `layout.tsx` is part of the cascade
contract (`admin.css` must follow `base.css` for `.admin-shell` to override
`.page-shell`); slices own their module files and rarely touch shared ones.

**Revisit trigger:** A design-token pipeline or theming requirement (e.g.
dark mode) outgrows plain custom properties.


## D028: Evidence-class derivation uses validated provenance presence

**Decision:** The UI derives each statement's evidence class
(`docs/UI_UX_CONTRACT.md` C-4) from typed signals first — the section key
plus the validated presence of a `resolved_claim` vs `derived_value`
provenance object — refined by runtime-checked optional `details` markers
(`temporal_assignment`, `data_status`). The optional, unvalidated
`provenance.root_type` field may corroborate but is never the discriminant.
`packages/contracts` is not changed for this; promoting a typed vocabulary
is tracked separately (issue #18).

**Fallback outcomes for permissive validation:** the payload validator
accepts statements with no provenance block at all
(`apps/web/src/lib/day-profile.ts:114`) and accepts both branches present
simultaneously (inclusive OR at `:103`). Both cases have defined, tested
outcomes: absent provenance classifies as `unclassified` (contract C-4.6);
both-branches-present classifies by root-gated section defaults that may
never raise epistemic strength — `recorded` requires a resolved claim,
`daily-average` requires a derived value, so both-present in the recorded
section stays `recorded` because a resolved claim exists, while a
derived-only statement there degrades to `unclassified` rather than
claiming record-ness.

**Context:** The published artifact carries class markers only inside
`details: Record<string, unknown>`; nothing stronger is typed today.

**Alternatives considered:** Trusting `root_type`; extending the contracts
package now; parsing statement prose; section key alone; requiring exactly
one provenance branch in the validator (rejected here — a validator
tightening is a contract-layer change owned by issue #18's discussion).

**Reason:** Rendering decisions must rest on signals the validator actually
guarantees; untyped markers can refine but must degrade safely (unknown →
section default, never a stronger claim, never a crash).

**Consequences:** `deriveEvidenceClass` is a pure, never-throwing function
with exhaustive unit tests covering every class, both permissive-validator
cases, marker degradation, and a lying `root_type`; a future typed-details
contract change (issue #18) can simplify it without changing rendered
classes.

**Revisit trigger:** Issue #18 lands a typed vocabulary, or a source
publishes statements whose class the current signals cannot distinguish.

## D029: Evidence panel is a native modal dialog

**Decision:** The per-statement evidence interaction (UI_UX_CONTRACT C-9)
is a native `<dialog>` opened by the canonical trigger, with jsdom's
missing showModal/close polyfilled in the shared test setup.

**Context:** C-9.2 requires focus containment while open, Esc closing, and
focus restoration to the trigger.

**Alternatives considered:** Styled `<details>` (no containment or focus
restoration); a hand-rolled focus-trap drawer (reimplements native
semantics); a third-party dialog library (new dependency, barred by C-12).

**Reason:** The native element supplies all three required behaviors plus
`::backdrop` without code or dependencies; styling covers the desktop
side-panel and mobile bottom-sheet presentations.

**Consequences:** Tests exercise the real open/close lifecycle; browsers
without `<dialog>` are outside the support matrix (Playwright's Chromium
projects define it).

**Revisit trigger:** A requirement for non-modal (parallel-reading)
evidence display.

## D030: Arrival numbering lives in client-module state

**Decision:** The travel choreography's initial-vs-adjacent distinction
(UI_UX_CONTRACT C-7.3) reads a counter in a client module
(`src/lib/travel-store.ts`), incremented inside the fetch callback, with
an exported test reset.

**Context:** App Router keys dynamic segments by param and remounts the
page subtree per date, so per-mount React state cannot distinguish a
first journey from an adjacent step; the eslint `react-hooks/refs` rule
correctly bars the ref-during-render alternative, and effect-set state
would flip the animation attribute after first paint.

**Alternatives considered:** Ref read during render (lint-barred, and
rightly); effect-set state (post-paint attribute flip restarts the
animation); context provider in the layout (same post-paint problem);
sessionStorage (persists across full reloads, which should replay the
initial reveal).

**Reason:** Module state survives soft navigation exactly as long as the
loaded bundle — the precise lifetime the distinction needs — and the
value is computed before setState so the attribute is stable from first
paint.

**Consequences:** Unit tests must reset the counter; a full page reload
replays the initial reveal, which is the intended behavior.

**Revisit trigger:** A router change that preserves page-subtree state
across dynamic-param navigation.

## D031: Publication tiers state how much a profile offers

**Decision:** Every publication manifest records a `publication_tier` —
`context_only`, `partially_enriched`, or `reviewed_enriched` — derived from
the published payload at publication time, stored as an indexed column, and
embedded in the hashed artifact so the database, the API response, the
coverage index, and the interface describe the same thing. Derivation is a
pure function of the payload sections: a populated `recorded_on_this_date`
yields `reviewed_enriched`; populated `curated_claims`,
`derived_comparisons`, or `wonder_and_progress` without a recorded event
yield `partially_enriched`; anything else — including annual daily
equivalents and period context — is `context_only`. A malformed payload
degrades to `context_only`.

**Context:** The archive is about to publish tens of thousands of dates
carrying annual demographic context and nothing else. Such a date is useful,
but it is not equivalent to 1964-03-27, and a boolean "published" flag would
erase that difference everywhere it matters.

**Alternatives considered:** Inferring richness in the frontend from section
contents (splits the definition across languages and cannot be queried);
storing the tier only in the artifact (not filterable for the coverage
index); storing it only in the database (the artifact and API would not
agree); requiring a recorded per-date human editorial review for
`reviewed_enriched` (no such record exists as data today).

**Reason:** A recorded event is the strongest signal the current pipelines
produce, and publication already gates it behind claim review and editorial
selection. Deriving from the payload keeps one definition, keeps the tier
consistent with the exact bytes served, and makes the archive's honesty
about sparseness structural rather than editorial.

**Consequences:** Publication injects the tier into the payload before
hashing, so identical content publishes identically across reruns; existing
manifests were backfilled from their immutable statement-evidence rows
rather than assumed; the frontend validator rejects a tier outside the
contract vocabulary instead of rendering it.

**Revisit trigger:** A per-date human editorial review becomes recorded
data, at which point `reviewed_enriched` should require it and the current
rule becomes `enriched`.

## D032: A standing editorial rule selects annual context for every date

**Decision:** Publishing a date whose only content is the reviewed annual
context of its year records editorial selections attributed to a named
standing rule (`standing-rule:annual-context-v1`) rather than to a person.
The rule's rationale is recorded on every selection: the reviewed annual
context for a year is selected for every date in that year, as period
context rather than a date-specific observation. Publication gates are
unchanged — license, pipeline, quality checks, and per-root editorial
selection are all still required.

**Context:** Editorial selection is per date, and the archive publishes
tens of thousands of dates whose content is identical annual context.
Selections existed for exactly one date (1964-03-27), so every other date
failed the publication gate.

**Alternatives considered:** Recording a human reviewer identity for each
date (a lie at 27,759 dates); weakening the eligibility gate for
context-only profiles (removes the accountability the gate exists for);
selecting annual context once per year rather than per date (the schema and
the product both treat selection as a per-date editorial act).

**Reason:** The rule keeps every published statement traceable to an
explicit, versioned editorial decision while being honest that the decision
was made once, by rule, for a whole year — not individually for each day.

**Consequences:** `ensure_annual_context_selections` is idempotent, so
reruns append no decision versions; `build_un_wpp_profile_content` gained
`require_editorial_selection=False` for callers that record selections from
the evidence it returns and then assert eligibility themselves — the
selection cannot exist before the content that names the roots to select.

**Revisit trigger:** A date receives human editorial attention, at which
point its selections should carry that reviewer and supersede the rule's.

## D033: Batch publication is ledgered, resumable, and rerun-safe

**Decision:** Batch publication records a run row and one entry per date
(`publication_batch_runs`, `publication_batch_entries`). Each date is
published in its own transaction; a failure is ledgered with its reason and
the run continues. `--resume` re-attempts what the ledger still owes,
`--retry-failed` re-attempts only failures, and `--dry-run` ledgers intent
without publishing. Unsupported years are rejected when the run is planned
rather than failing date by date.

**Context:** The archive is ~27,759 dates. A run that dies at date 18,403
must not restart from the beginning, and a rerun must not accumulate
versions.

**Alternatives considered:** A single transaction per run (one bad date
loses everything); no ledger, inferring progress from published manifests
(cannot distinguish "never attempted" from "attempted and failed", and
loses failure reasons); failing the whole run on the first bad date.

**Reason:** Rerun safety comes from publication itself being idempotent by
content (D031/AA0); the ledger adds the operational memory an interrupted
run needs to finish, and the failure record an operator needs to act.

**Consequences:** A date whose publication committed before the process
died is re-attempted on resume and recorded as `unchanged` — verified by
killing a real 1972 run mid-flight and resuming it to 366 dates at version
1. Ledger writes commit separately from publication, so the ledger can lag
a crash by one entry but never overstate progress.

**Revisit trigger:** Publication becomes parallel across dates, which would
require the ledger to record worker ownership.

## D034: Coverage is indexed per date, not inferred per request

**Decision:** A `coverage_entries` table records, for each date a reader
would actually be served: profile type, publication tier, whether a recorded
event is present, per-section published-statement counts, quality floor,
review status, and the index version. It is maintained as the final step of
publication and can be regenerated deterministically
(`make rebuild-coverage`). Counts come from immutable
`publication_statement_evidence` rows; the quality floor comes from the
served payload and is the weakest grade the profile rests on, not its best.
`GET /api/v1/coverage` and `GET /api/v1/coverage/{date}` serve it, the
latter including the nearest enriched and nearest recorded-event dates in
both directions.

**Context:** With every 1950–2025 date carrying annual context, "is
anything published?" stops distinguishing anything. Navigation needs to know
where the evidence actually is, and the landing page needs to disclose the
archive's real shape rather than implying uniform richness.

**Alternatives considered:** Computing richness per request from manifests
and artifacts (a scan of tens of thousands of rows and files per
navigation); a boolean published-dates index (superseded, and useless once
the archive is dense — this closes issue #19); inferring richness in the
frontend from section contents (splits the definition across languages and
cannot answer "nearest enriched date").

**Reason:** Publication already knows exactly what it published, so the
index is a by-product rather than a derivation; the nearest-enriched
queries that make navigation honest are index scans instead of archive
walks.

**Consequences:** Dates without a published profile are absent from the
index rather than present-and-empty, so the API can distinguish "no profile"
from "sparse profile"; a rebuild drops entries whose newest publication no
longer serves readers; publication carries one extra write per date.

**Revisit trigger:** Coverage needs per-section quality or provenance
detail, at which point the entry grows rather than the profile being
re-read per request.

**Amendment (PR #43 review, 2026-07-26):** four properties were added
because the first implementation could describe an archive that did not
exist.

- *Review status is derived from editorial-selection rows, not from the
  presence of evidence.* The vocabulary is `reviewed` (a reviewer other
  than a standing rule selected this date's content), `rule_selected` (a
  standing rule did), and `unreviewed` (no editorial decision is
  recorded). Calling a recorded event "reviewed" borrowed the credibility
  of a review that does not exist as data.
- *Every path that makes a profile readable indexes it* — publication,
  idempotent republication, and reconciliation repair — through a single
  seam. A profile the day endpoint serves while coverage reports it
  missing is a navigation lie, and the idempotent path meant re-running
  the publishers could not heal a missing index.
- *A rebuild refuses to index an artifact it cannot read*, and reports it.
  The day endpoint fails for such a date; sending readers there because
  the database row looks fine is the same dishonesty in a different
  place. Rebuilds also re-read each date under its publication lock, so a
  correction that lands mid-rebuild wins.
- *The migration backfills an existing archive* (leaving quality floors
  null, since they live in the artifacts), and the summary aggregates in
  SQL rather than materializing every entry — that response is
  constant-size and the archive it describes is not.

The coverage response shapes live in `packages/contracts/src/index.ts`,
so the vocabulary the API sends is the vocabulary the UI can name.

**Amendment (PR #43 review round 2, 2026-07-26):** four further properties,
three of which only bite at archive scale or under concurrency.

- *A rebuild holds one date's publication lock at a time and releases it.*
  Publication's transaction-scoped lock is right for a single date; a
  rebuild walks the whole archive in one transaction, so transaction-scoped
  locks would accumulate roughly 27,759 of them, exhaust the lock pool, and
  block corrections to early dates for the length of the run.
- *A date absent from the rebuild's snapshot is not assumed stale.* It may
  have been published while the rebuild ran, so its entry is re-checked
  under its lock and kept when it is genuinely live. Deleting it would
  leave the day endpoint serving a profile coverage reports as missing.
- *Reconciliation passes the payload it already verified* into the index,
  rather than letting the entry keep the predecessor's quality floor while
  pointing at the new manifest.
- *The quality floor is ordered by an explicit rank, not by string
  comparison* — under which `A+` sorts above `A` and would be reported as
  the floor. A grade outside the known vocabulary cannot be ordered against
  it and is therefore treated as the weakest thing present: "we cannot
  establish how good this is" must never read as "good". The profile
  contract still permits any grade string; this narrows only the ordering,
  not the contract.

The migration's section counts are filtered to the contract's seven keys,
matching the publisher, so identical archive state cannot describe itself
differently depending on whether it was backfilled or rebuilt. The summary
endpoint emits `supported_range` as `{minimum, maximum}`, matching both the
shared contract and the sibling out-of-range response.

**Split (PR #43 review round 3, 2026-07-26):** at ~950 net non-test lines
the change exceeded the repository's ~800-line split threshold, so it lands
as two slices: the index and its maintenance (schema, derivation,
publication integration, `make rebuild-coverage`), then the coverage API
(the reader-facing record with nearest-richer neighbours, the archive
summary, both routes, the shared contract types, the web proxies). The seam
is maintenance versus reads, and `coverage_entry()` — a single indexed row —
is the boundary the API slice builds on.

This trades §4's preference for vertical slices against §5's size law. The
first slice ships an operator-visible improvement, a maintained and
regenerable index plus a migration that backfills an existing archive,
rather than a user-visible one. Three review rounds and twenty-four
findings on a single PR is the evidence the size law exists for.

**Amendment (PR #43 review round 6, 2026-07-26): the index carries no
generation counter.** `index_version` was removed rather than corrected a
third time. It produced a defect in three separate review rounds — an
ordinary publication resetting one date to an older generation, two
overlapping rebuilds writing different generations, and a date published
mid-rebuild retaining the previous one — and a full-table `MAX()` on every
publication besides.

It was never load-bearing. Under the per-date lock every writer re-reads
the current manifest inside the lock and writes what is true at that
moment, so a stale write cannot occur and there is no ordering to defend.
The generation counter existed only to resolve disagreements it created.
`refreshed_at` records when a row was last written, which is what the
diagnostic need actually was.

The principle, applied here and to review status: a smaller claim that can
be proved beats a larger one that keeps needing correction.

**Amendment (PR #43 review round 7, 2026-07-26): the index records only
what its own evidence proves.** `quality_floor` and `review_status` were
cut from the entry and deferred to issue #45. Both required either an
artifact read or an editorial join, threaded through six write paths —
publication, idempotent republication, two reconciliation-repair paths,
the rebuild, and the migration backfill — and each new writer was another
chance for them to disagree. Between them they accounted for most of a
seven-round review, with `review_status` alone corrected four times, each
round finding a different side of the governance key
`(profile_date, section_key, root)`.

What remains — profile type, publication tier, recorded-event flag, and
per-section published-statement counts — is derived entirely from the
manifest and its immutable statement evidence. No writer needs a payload
or an editorial lookup, so there is nothing to thread and no second source
to disagree with. That is also exactly what navigation needs: how rich a
date is, and whether it holds a recorded event.

Applied here for the third time in this review, and stated as the rule:
**a smaller claim that can be proved beats a larger one that keeps needing
correction.**

## D035: A generated profile is not a reviewed one

**Decision:** The golden set's `publication_status` vocabulary is explicit
and validated: `not_generated`, `context_published`, and
`published_and_validated`. The canary records the dates it publishes as
`context_published`, which does **not** count toward `published_count` and
therefore cannot satisfy `release_ready`. `manual_review_status` is
validated against `pending_human_review` / `reviewed` for the same reason.
An unrecognised value in either field now fails the file.

**Context:** Slice AA4 publishes the 66 golden dates that current pipelines
support (1900–1949 has no annual-context pipeline, so those 34 stay
honestly unpublished). Before AA5 publishes 27,759 dates, the machinery
that marks dates as published must not be able to mark the release gate as
satisfied on its way past.

**Alternatives considered:** Reusing `published_and_validated` for
canary-published dates (the mass run would then tick the release gate,
which is precisely the outcome the gate exists to prevent); leaving the
status vocabulary unvalidated (a typo read as "not published", silently
understating the set instead of failing it).

**Reason:** Release readiness is a claim about human review. Publication
machinery can prove that a profile was generated and validated by
automated checks; it cannot prove anyone read the page.

**Consequences:** `make validate-golden-set` reports
`context_published_count` alongside `published_count`, and
`release_ready` stays false until a human review actually happens.
`record_canary_publication` never downgrades a `published_and_validated`
date.

**Revisit trigger:** Per-date human review becomes recorded data, at which
point `reviewed` can be derived rather than hand-maintained.

## D036: The canary validates meaning, not shape

**Decision:** `validate_context_payload` checks published profiles for
defects a reader would notice: the daily-equivalent denominator matches the
year's real length **and** the prose that states it agrees with the number;
a modeled year says it rests on a projection and an estimated year does not
claim to; a daily equivalent disclaims being an observation; every section
has a declared state; a section declared unsupported carries a reason and
holds no content; and a `context_only` profile carries no recorded event.

**Context:** Schema validity and hash integrity were already enforced, and
neither would catch a profile telling a reader that 1952 had 365 days or
that a projection is an observation. The canary is the last cheap place to
find such a defect: after AA5 the same defect is 27,759 wrong pages.

**Alternatives considered:** Trusting the publishers' unit tests (they
prove the generator's logic, not that a published artifact is right);
manual inspection alone (66 dates now, 27,759 later).

**Reason:** The product's value is evidential honesty, so the canary's
checks are about what a statement claims, not whether it parses.

**Consequences:** The value and the sentence describing it are cross-checked
against each other, so a correct number under a wrong sentence fails.
Verified against real published artifacts: corrupting a leap-year
denominator, contradicting the prose denominator, stripping projection
wording from a 2025 date, and removing an unsupported section's reason each
produce an issue.

**Revisit trigger:** A new evidence class ships whose honesty depends on
properties these checks do not cover.

## D037: Missing conflict context is an absence; unreviewed conflict context is a failure

**Status:** Accepted (2026-07-26, epic #51 / UC2)

**Context:** Carrying UCDP annual conflict context into every context
profile means asking, for each of 27,825 published dates, what to do when
the year's conflict content is not available. Two very different situations
produce that same surface appearance: the archive genuinely has nothing for
the year, and the archive has records that have not been reviewed yet.

**Decision:** They get opposite answers. A year the release does not cover —
and a deployment that has not ingested UCDP at all — publishes with no
conflict statement, and the profile says nothing whatsoever about armed
conflict. A year whose records exist but are unaccepted, unreviewed or
underived fails the date closed
(`services/api/app/ucdp.py:optional_annual_context`).

**Alternatives considered:** Treating every unavailable case as an absence
(the publisher would silently drop reviewed evidence, and a republication
pass would quietly strip conflict context from dates that had it);
treating every unavailable case as a failure (a deployment without UCDP
could publish nothing at all, and the archive's demographic context would
be hostage to a second source).

**Reason:** Absence and failure look identical in the output and are
opposite in meaning. Publishing a quieter profile over reviewed evidence
hides the thing the archive exists to show; refusing to publish a date
because a source it never had is missing withholds evidence we do have.

**Consequences:** `optional_annual_context` returns `None` only for the two
genuine-absence cases and raises for everything else. Both sides are pinned
by tests: a covered year carries the statement, an uncovered year publishes
without it, and an ingested-but-unreviewed year raises rather than
publishing quietly.

**Revisit trigger:** A third source joins the context profile, at which
point this rule should be lifted out of the UN WPP publisher into a
composer that owns it for every source.

## D038: A standing rule fills gaps; it never overrules a human

**Status:** Accepted (2026-07-26, epic #51 / UC2)

**Context:** `standing-rule:annual-context-v1` selects a year's reviewed
context for every date in that year, because no human was ever going to
visit 27,825 dates individually (D032). Once conflict context travels the
same path, the rule can encounter a date where a reviewer already recorded
a decision about that exact content.

**Decision:** Where the latest editorial decision on a root is anything
other than a selection, the content is omitted and the date publishes
without it (`services/api/app/un_wpp.py:_root_declined_for_date`). The
standing rule does not record a competing decision, and publication does
not fail.

**Alternatives considered:** Failing the date closed, as a rejected
demographic root does — but the demographic content is the profile's reason
to exist, while conflict context is an addition, and blocking a date over
an optional section punishes the reader for a reviewer's judgement.
Overwriting the rejection was never a candidate.

**Consequences:** A reviewer can decline conflict context for one date
without breaking it and without their decision being reverted by the next
publication pass. The rejection stays the latest decision in
`editorial_selections`, which is the audit trail — no separate record is
needed.

**Revisit trigger:** A section becomes load-bearing enough that silently
omitting it would mislead, at which point that section needs the
fail-closed treatment instead.

## D039: The first comparison publishes on one date, because the tier says something

**Status:** Accepted (2026-07-27, epic #51 / UC4)

**Context:** UC4 publishes the archive's first app-derived comparison into
`derived_comparisons`. The obvious move is to carry it onto every date, the
way UC2 carried conflict context. But `derived_comparisons` is one of
`EDITORIAL_SECTIONS` (`services/api/app/services.py:1242-1246`), so
populating it promotes a profile from `context_only` to
`partially_enriched`.

**Decision:** The comparison publishes on the golden date only, which is
already `reviewed_enriched` and whose tier therefore does not move. Carrying
it archive-wide is deferred to #62, which is a contract question about
what the tier means.

**Alternatives considered:** Publishing it on all 27,759 dates — every
context profile would become `partially_enriched`, telling readers those
pages carry curated or comparison content beyond annual context when what
they carry is one mechanically-derived annual number. The coverage index,
the enriched-navigation controls and the landing disclosure all key off the
tier, so the archive's one genuinely enriched date would stop being
distinguishable. Changing what the tier means so the promotion is
justified — that is a contract change, and per the working agreement it is
its own deliberate PR rather than a side effect of a feature.

**Reason:** The tier is a claim about how much a page offers. Promoting
every page because a derived annual comparison was added would make the
claim false in exactly the way this product exists not to be false, and it
would do so silently.

**Consequences:** One date carries a comparison. The derivation runs for all
80 years, so the data is ready when the breadth question is decided —
deriving is not publishing. A minimum reference period of 20 years means the
committed single-year fixture publishes no comparison, so development and CI
exercise the absence path.

**Revisit trigger:** The tier vocabulary gains a level that distinguishes
mechanically-derived annual content from curated per-date content, at which
point the archive-wide question can be reopened without overstating.

## D040: Comparisons publish archive-wide, and the tier stays neutral

**Status:** Accepted (2026-08-01, epic #64 / MD3, closes the #62 question)

**Context:** D039 published the first app-derived comparison on the golden
date alone, because `derived_comparisons` was an `EDITORIAL_SECTION` and
populating it promoted a profile from `context_only` to
`partially_enriched`. Carrying it archive-wide was deferred to #62 as a
contract question: what does the tier mean?

**Decision:** The tier counts **date-specific** content, not merely
*editorial* content. A period comparison is annual context — it describes
the year, not the day — so it no longer promotes any profile, and the
standard context publish path carries it on every eligible date. Answered
D039's revisit trigger not by adding a tier level but by correcting what the
existing tiers count.

**Mechanism:** `derive_publication_tier` promotes an editorial section only
when it holds a statement `_is_date_specific` accepts
(`services/api/app/services.py`). Date-specificity is an **allow-list** of
temporal assignments (`DATE_SPECIFIC_ASSIGNMENTS = {direct_record, reported,
inferred, modeled_period_allocation}`), not a deny-list: the comparison
carries `temporal_assignment=period_context`, which is absent from the list,
so it never promotes. An allow-list also fails safe — a temporal assignment
added later defaults to not promoting (understating), never to silently
reclassifying the archive. `publish_context_profile`
(`services/api/app/un_wpp.py`) wires `optional_conflict_comparison` into the
standard path, gated on the conflict context it compares being present.

**Alternatives considered:** Adding a tier level between `context_only` and
`partially_enriched` for mechanically-derived annual content — more
vocabulary for readers to learn, when the real rule is simply that the tier
measures per-date richness. A deny-list of period markers — it would have
promoted `uniform_period_allocation` (annual averages) and every unmarked or
future assignment, the exact silent-reclassification failure this avoids.

**Reason:** The tier is a claim about how much a page offers about its day.
One mechanically-derived annual number is not that, however many a page
carries, so counting it would make the claim false in the way this product
exists not to be.

**Consequences:** Comparisons are safe to publish on all ~27,759 dates
without moving any discovery signal — nearest-enriched, random-enriched, the
tier buckets, or the recorded-event flag. Proven offline over a whole
multi-year archive by `test_the_whole_archive_stays_context_only_when_comparisons_publish`
and, for the golden date, `test_the_golden_date_stays_enriched_in_the_index`
(`services/api/tests/test_tier_neutral_comparisons.py`). The archive-wide
republish against the real release remains an operator pass
(`docs/runbooks/archive-comparison-republish.md`); the code guarantee lands
here.

**Known follow-up:** The published comparison statement carries its
neutrality only through `temporal_assignment`; its stored value also records
`date_specific: false`, but that flag is not copied into the published
`details`, so `_is_date_specific`'s explicit `date_specific` guard never
fires for the real payload. Carrying it into the published statement would
add a second, independent guard. Deferred (it changes the payload hash, so it
belongs with a republish); tracked in #62.

## D041: A recorded event publishes through one source-agnostic spine, and a pass consumes human decisions rather than making them

**Status:** Accepted (2026-08-03, epic #71 / Golden 100 G2b)

**Context:** Until now the only recorded-event publisher was
`publish_golden_profile` (`services/api/app/usgs.py`) -- hardcoded to
`GOLDEN_DATE`, a `ProfileType` literal, and nine USGS predicates. The Golden
100 arc adds a second source (Wikidata), and later an operator run over the
real 99 dates, so a recorded event must be publishable from any reviewed
source on its own date without forking that publisher. The shared question:
what is a pass allowed to do on the path to a published recorded event?

**Decision:** A recorded event publishes through the shared, source-agnostic
spine (`publish_day_profile`), with the profile **date and type derived from
the event's own occurrence** (`profile_type_for_date`), never a literal. The
publishing pass **consumes** the human stages before it -- claim acceptance
(D019) and editorial ranking -- and fabricates neither (D038): it publishes
only what a human accepted and a human ranked, and on a date that already
holds a recorded event it **defers** to a durable merge-review task rather
than publish a competing one or decide the merge itself.

**Mechanism:** `publish_wikidata_event` (`services/api/app/wikidata.py`)
requires the core candidate claims `ACCEPTED` (D019), derives the occurrence
date from P585 and the profile type from it, and before minting anything
calls `published_recorded_event_on` (G1's collision primitive): a collision
with a recorded event that is not this entity's own opens or reuses a
`MERGE-REVIEW:` `ReviewTask` on the identity claim and returns
`deferred_to_merge_review`, creating no competing `Event`, manifest, or
profile. Otherwise it builds recorded statements whose text derives only from
the resolved values (honest data, §12), and calls
`assert_release_publication_eligible` -- which requires a human editorial
selection for every published root, so an unranked candidate is refused, the
pass never recording the selection itself. Re-publishing the same entity's
event is idempotent by content hash.

**Alternatives considered:** Generalizing `publish_golden_profile` in place
-- it would fork the golden publisher's USGS-specific statement derivation and
risk the one real enriched date; a sibling caller of the shared spine reuses
the two-phase publish, idempotency, and coverage indexing without touching
golden. Having the pass record the editorial selection from the human's claim
acceptance -- convenient, but it manufactures a human decision that was never
made, the exact overstep D038 forbids; requiring a separate human ranking
keeps `review_status` honest.

**Consequences:** Recorded-event publication is now source-neutral: the
operator run (G4) and any future source publish through the same gated spine.
Proven offline against the committed `Q749610` fixture by
`test_publish_marks_the_date_enriched_from_the_resolved_candidate` and
`test_publish_defers_on_recorded_event_collision`
(`services/api/tests/test_wikidata_publish.py`). The real 99-date enrichment
-- live Wikidata ingest, review, publish, canary -- remains an operator arc
(G4, epic #71), like the UCDP/MD4 passes.

## D042: A human's answer to a collision is a durable record about events, not a closed task

**Context:** G2b's publish path defers on a recorded-event collision by opening
a `MERGE-REVIEW:` `ReviewTask` asking a human to choose merge, supersede, or
distinct-event (D041). The task had nowhere to put the answer. The collision
guard reads `published_recorded_event_on` and nothing else, so a human closing
the task changed nothing observable: the next publish attempt collided, opened
the task again, and deferred again. The workflow asked a question it could not
consume.

**Decision:** A collision answer is a **versioned, pair-specific, human-authored
record about two canonical events**, and it is the only thing that lets a second
recorded event publish on a date.

The adjudication identifies **events, never publication manifests**. A manifest
is a versioned publication artifact, so a decision keyed on one would stop
applying the moment the date was republished -- which is exactly when it still
has to hold, since republication is what an enrichment does.

**Mechanism:** `event_identity_adjudications`
(`services/api/app/governance.py`, migration `20260803_0017`) stores the pair
canonically ordered, so `event_a_id < event_b_id` makes the unordered pair
unique and rejects self-pairs in one constraint. History is append-only: a
changed decision appends a version carrying `supersedes_adjudication_id`, and
every foreign key is `RESTRICT`. `merge`/`supersede` require a surviving event
that is a member of the pair; `distinct_event`/`deferred` refuse one. The
profile date is derived from both events' own primary occurrences, never taken
from the caller.

`adjudicated_distinct` is what the guard consumes, and it fails closed on every
other input: no record, a superseded `distinct_event`, a non-`distinct_event`
outcome, a non-human author, or a decision about a different pair.
`_collision_adjudication` (`wikidata.py`) requires a current human
`distinct_event` against **every** event behind the colliding manifest, and
treats a manifest whose events cannot be resolved as a deferral -- otherwise
"every event is adjudicated" would be vacuously true over an empty set and
bypass the guard precisely when the collision is least understood. A recorded
non-permitting decision returns `blocked_by_adjudication` rather than opening
another review task, ending the duplicate-task treadmill.

The table is append-only *in the database*, not merely in the writer: the
`event_identity_adjudications_append_only` trigger reuses
`prevent_governance_record_mutation` (migration 0008), the same function already
guarding `source_release_licenses`, `claim_review_decisions` and
`editorial_selections`. The unique history index stops two rows sharing a
(pair, version) and says nothing about an `UPDATE` rewriting the latest decision
in place; a governance record that skipped this would be the only mutable one.

A merge-review task records the collision it asked about
(`review_tasks.context_manifest_id`) rather than naming it only in its rationale
prose, and one predicate -- `_task_concerns` -- decides whether a task is about
the collision at hand. Every path that touches a task routes through it: opening
reuses only a task about this collision and retires one whose collision has
moved (otherwise refusing a stale task would leave the candidate in a
publish/reject loop), and resolving requires a task about this collision,
counting resolved ones so a *retry* is checked against the same subject rather
than sailing past the guard because nothing is open. The comparison is on *events*, not manifest identity:
republishing the same recorded event mints a new manifest, and refusing that
would strand a reviewer on any date republished for an unrelated reason, while
comparing events catches the case that matters -- an answer landing on a pair
nobody evaluated. A task with no recorded subject is refused rather than
defaulted to the current collision.

`candidate_cli adjudicate` is the operator's half of the workflow. `publish`
opens the task that asks whether two events are the same event, and without a
command that answers it the collision defers forever whatever a reviewer decides
-- the decision would be reachable only from a test.

`is_human_reviewer` moves into `governance` as the one classification rule, by
prefix rather than by an enumerated identity; `derive_review_status` delegates to
it. The previous copy named a single rule identity, so each new standing rule
would have counted as a person until somebody remembered to add it -- and that
drift reports unreviewed content as reviewed.

**Alternatives considered:** Keying the adjudication on the colliding manifest
-- simpler to look up, but it silently expires on republication. Letting a
`distinct_event` decision clear the date rather than the pair -- that is exactly
the blanket permission the guard exists to withhold.

**Consequences:** A human `distinct_event(A, B)` now lets B publish past A's
collision, and an unrelated C on the same date still defers. Proven by
`test_a_human_distinct_event_decision_lets_publication_pass_the_collision` and
`test_a_decision_about_another_pair_does_not_unlock_this_collision`
(`services/api/tests/test_wikidata_publish.py`) plus
`services/api/tests/test_identity_adjudication.py`.

Publishing past a collision currently **replaces** the date's
`recorded_on_this_date` section with the newly published event's statements, so
the superseded event stops being the headline. That is G3b's job (#79): the
publisher resolves the featured event, builds the section from that event's
predicates, and binds the featured identity decision to the manifest so
`derive_review_status` can see it. Until G3b lands, the bypass only activates
when a human explicitly records a `distinct_event` decision, and G4 does not
begin until both slices merge.

The single-choice featured-event writer this needs -- `record_editorial_selection`
versions per `(date, section, root)`, so two roots can each read as selected on
independent counters -- is the immediately following slice, split out to keep this
PR's review surface inside the repo's scope rule.
