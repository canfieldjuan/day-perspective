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

**Context:** The published artifact carries class markers only inside
`details: Record<string, unknown>`; the payload validator
(`apps/web/src/lib/day-profile.ts:103`) guarantees at least one typed
provenance branch but never checks `root_type`.

**Alternatives considered:** Trusting `root_type`; extending the contracts
package now; parsing statement prose; section key alone.

**Reason:** Rendering decisions must rest on signals the validator actually
guarantees; untyped markers can refine but must degrade safely (unknown →
section default, never a stronger claim, never a crash).

**Consequences:** `deriveEvidenceClass` is a pure, never-throwing function
with exhaustive unit tests (slice B1); a future typed-details contract
change (issue #18) can simplify it without changing rendered classes.

**Revisit trigger:** Issue #18 lands a typed vocabulary, or a source
publishes statements whose class the current signals cannot distinguish.
