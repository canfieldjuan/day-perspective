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
than returning to an earlier version. Branching would require an explicit
canonical-successor product rule before being enabled.

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
