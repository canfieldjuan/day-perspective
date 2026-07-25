# Data Dictionary

Foundational entity tables use UUID primary keys and UTC `timestamptz` where a
timestamp is stored. Relationship tables may instead use composite UUID foreign
keys, such as `resolved_claim_evidence`. Provenance foreign keys use `ON DELETE
RESTRICT`; imported evidence and published history are retained instead of
cascade-deleted.

## Constrained values

| Type | Values |
| --- | --- |
| `claim_assertion_status` | `imported`, `candidate`, `in_review`, `accepted`, `rejected`, `superseded`, `retracted` |
| `temporal_precision` | `day`, `month`, `year`, `decade`, `interval`, `unknown` |
| `temporal_assignment` | `direct_record`, `reported`, `inferred`, `uniform_period_allocation`, `modeled_period_allocation`, `editorial_context`, `unknown` |
| `date_role` | `occurred`, `began`, `ended`, `reported`, `discovered`, `published`, `predicted`, `commemorated` |
| `data_status` | `reported`, `estimated`, `modeled`, `provisional`, `final`, `missing`, `withdrawn` |
| `missing_reason` | `not_collected`, `not_available`, `not_applicable`, `withheld`, `invalid`, `unknown` |
| `resolution_method` | `single_source`, `corroborated`, `editorial_review`, `methodological_derivation` |
| `source_lineage_relationship` | `republished`, `transcribed`, `extracted`, `aggregated`, `derived` |
| `comparability_status` | `comparable`, `partially_comparable`, `not_comparable`, `unknown` |
| `impact_directness` | `direct`, `indirect`, `modeled`, `contextual` |
| `publication_status` | `draft`, `published`, `superseded`, `withdrawn` |
| `profile_type` | `limited_historical`, `standard_statistical`, `enhanced_structured` |
| `legal_review_status` | `not_required`, `pending`, `approved`, `restricted`, `rejected` |
| `pipeline_run_status` | `running`, `succeeded`, `failed`, `cancelled` |
| `quality_check_status` | `passed`, `failed`, `warning`, `skipped` |
| `review_task_status` | `open`, `in_progress`, `resolved`, `dismissed` |
| `review_task_priority` | `low`, `normal`, `high`, `blocking` |
| `evidence_position` | `supporting`, `dissenting` |
| `derived_input_role` | `primary`, `supporting`, `comparison` |

## Provenance, workflow, and methodology

| Table | Important columns and relationships | Constraints and deletion rule |
| --- | --- | --- |
| `sources` | `slug`, name, publisher, canonical URL, legal-review status. Parent of releases. | `slug` is unique. A source with releases cannot be deleted. |
| `source_releases` | Required source FK, release label, source/raw URI, raw SHA-256, raw-record count, retrieve/ingest/publish time, legal status, pipeline run, metadata. | Unique source/label and source/checksum. An update/delete trigger makes an ingested release immutable; import services reject a declared checksum that disagrees with supplied raw bytes. |
| `source_lineage` | Child and parent release FKs, relationship enum, optional methodology, note. | Self-lineage is forbidden; the edge is unique. It records republished and derived source material. |
| `pipeline_runs` | Pipeline name, code/configuration hash, running/terminal status, timestamps, details. | Status is `running`/`succeeded`/`failed`/`cancelled`; configuration hash is required; terminal time cannot precede start. |
| `quality_checks` | Required pipeline-run FK, check/status, subject type/ID, detail, time. | Status is `passed`/`failed`/`warning`/`skipped`. |
| `quality_assessments` | One or more target FKs among release/claim/observation/derived value/`target_methodology_id`, optional assessment `methodology_id`, legal status, kind, score, findings. | At least one target is required; score is 0 through 1. Target methodology is distinct from the methodology used to perform the assessment. |
| `review_tasks` | Exactly one claim or resolved-claim target, status, priority, rationale, assignee, timestamps. | Status is `open`/`in_progress`/`resolved`/`dismissed`; priority is `low`/`normal`/`high`/`blocking`; target exclusivity and completion-time ordering are checked. |
| `methodologies` | Versioned slug/name/description, method kind, formula, code version, definition hash, legal status. | `(slug, version)` is unique; downstream calculations and manifests retain their method FK. |

## Claims and resolution

| Table | Important columns and relationships | Constraints and deletion rule |
| --- | --- | --- |
| `claims` | Required source-release/record locator, assertion status/type/text/JSON, temporal start/end/precision/assignment/date role, data status/missing reason, supersedes link, pipeline run. | Claims cannot exist without a release. Missing claims hold no assertion payload and require a reason. Dates and self-supersession are checked. |
| `resolved_claims` | Canonical key/version/value, resolution method/rationale/comparability, optional methodology, supersedes resolved claim. | Key/version is unique; source evidence is retained through the join table. |
| `resolved_claim_evidence` | Required resolved-claim and claim FKs plus `supporting` or `dissenting` stance and note. | Composite primary key prevents duplicate evidence. A deferred database trigger requires at least one supporting claim on every resolved claim after insert, update, retarget, or delete. |

## History, events, places, and entities

| Table | Important columns and relationships | Constraints and deletion rule |
| --- | --- | --- |
| `geographies` | Stable key and kind. | Stable key is unique; historical changes live in versions. |
| `geography_versions` | Required geography and provenance-resolved-claim FKs, historical name/code, valid date range, optional PostGIS multipolygon. | Date order is checked and PostGIS exclusion prevents overlapping versions for a geography. |
| `events` | Required resolved-claim FK, type, canonical title, summary, data status. | A resolved claim creates at most one canonical event. Event impacts are deliberately absent from this table. |
| `event_times` | Required event and provenance FKs, start/end, precision, assignment, date role, primary flag, label. | Events may have many rows; at most one primary time per event. |
| `event_locations` | Required event/provenance FKs, optional geography version and/or PostGIS point, role, label. | Events may have many locations; at least geography version or point is required. |
| `people` | Required resolved-claim FK, canonical name, optional biography summary. | One canonical person per source resolution; delete is restricted. |
| `organizations` | Required resolved-claim FK, canonical name, optional kind. | One canonical organization per source resolution; delete is restricted. |
| `entity_aliases` | Alias/language plus exactly one person, organization, geography, or event target and provenance. | `num_nonnulls` enforces one target. |
| `external_identifiers` | Namespace/identifier plus exactly one person, organization, geography, or event target and provenance. | One target is required and namespace/identifier is unique. |

## Metrics, observations, impacts, and derivations

| Table | Important columns and relationships | Constraints and deletion rule |
| --- | --- | --- |
| `metrics` | Stable metric key, display name, unit, definition, data status, resolved-claim provenance, optional methodology. | Metric key is unique; delete is restricted by observations/derivations. |
| `observations` | Required metric/source-release FKs, optional geography/resolution, period, precision/assignment/role, numeric or text value, data/missing status. | A non-missing observation needs a value; a missing one needs a reason and null value. Numeric zero remains a valid observation. |
| `event_impacts` | Required event/resolved-claim FKs, optional metric/methodology, directness, narrative, numeric value, data/missing status. | Impacts are separately stored, with missingness constraints, rather than on events. |
| `metric_coverage` | Required metric/source-release/resolved-claim FKs, optional geography, period, nullable coverage fraction, data/missing and comparability status. | Non-missing fractions are 0 through 1; missing coverage requires a null fraction and reason, so unknown coverage is never stored as zero. |
| `derived_values` | Optional metric/geography/resolution, required methodology, result kind/value, period, assignment, data/missing/comparability status, input fingerprint and calculation version. | Derived missingness is explicit; input fingerprint is a SHA-256 value; a deferred trigger requires direct resolved-claim provenance or at least one durable input row, including after an input is retargeted. |
| `derived_value_inputs` | Required derived-value FK and exactly one observation or resolved-claim input, with input role. | The role is `primary`/`supporting`/`comparison`; this additional table preserves durable, foreign-keyed derivation inputs. |

## Publication and correction

| Table | Important columns and relationships | Constraints and deletion rule |
| --- | --- | --- |
| `publication_manifests` | Date/type/version, status, content and source-snapshot SHA-256 hashes, storage URI, methodology/code version, superseded manifest, metadata, timestamps. | Date/type band and hashes are checked. Final manifest updates/deletes are rejected by trigger; a supersession must point to one published manifest of the same date and profile type, yielding a linear correction chain. |
| `publication_statement_evidence` | Required manifest and statement JSON path, exactly one resolved-claim or derived-value FK, canonical `evidence_snapshot` JSON, and `evidence_snapshot_hash`. | Unique manifest/path and target exclusivity enforce one provenance root per published statement. Snapshot JSON must be an object and its hash must be SHA-256 shaped. The publication service derives both the statement hash and manifest source-snapshot hash. The trigger protects both the old and new manifest on update, so a final mapping or snapshot cannot be retargeted, rewritten, or removed. Migration `20260723_0005` refuses to invent snapshots if legacy evidence rows exist. |
| `day_profiles` | Date/type, required publication-manifest FK, content hash, predecessor profile, time. | Trigger requires a matching published manifest and content hash, checks matching predecessor identity, permits one successor per predecessor, then rejects subsequent updates/deletes. |
| `corrections` | Optional correction claim plus required original/replacement manifest FKs and rationale. | Original/replacement differ. Trigger requires both manifests be published, same date/type, explicit manifest supersession, and matching day-profile supersession. Repeating the same pair and rationale returns the existing row; a conflicting rationale is rejected. |

## Date-band and cross-table rules

`limited_historical` is valid only from 1900 through 1949,
`standard_statistical` only from 1950 through 1988, and
`enhanced_structured` only from 1989 through 2025. Both manifests and day
profiles enforce those bands. Publication status is distinct from data status,
and temporal assignment is distinct from temporal precision.

## Phase 2 Schema Additions (`20260724_0007`)

### `raw_source_records`

One immutable source record retained inside one immutable source release.
`source_record_id` is unique within its release. `source_record_locator` is the
public record URL; `raw_storage_uri` is an internal storage key;
`raw_checksum_sha256` identifies the canonical validated record payload, while
the release checksum identifies the exact retrieved file bytes.
`schema_version` identifies the adapter validation contract; `payload_json`
retains the validated record. Update and delete are rejected. Release deletion
is restricted. `raw_storage_uri` points to the canonical record bytes whose
SHA-256 is stored on the same row; it is not the enclosing release object.

### `claims` additions

- `source_record_hash_sha256`: required 64-character SHA-256. A database insert
  trigger may derive it from the immutable release checksum only for a
  single-record release. Migration `0007` and direct inserts fail closed for
  multi-record releases unless the actual record hash is supplied.
- `unit`: predicate unit or measurement scale; nullable for nonnumeric predicates.
- `lower_bound`, `upper_bound`: optional numeric uncertainty bounds. When both exist, lower must not exceed upper.

### `event_times` additions

- `exact_timestamp`: timezone-aware occurrence instant.
- `local_date`: separately assigned civil date.
- `local_date_provenance_resolved_claim_id`: resolved local-date claim that
  supports the civil-date projection, distinct from the UTC occurrence claim.
- `timezone_name`: IANA rule set used for conversion.
- `utc_offset_minutes`: offset at the historical instant.
- `interpretation`: public explanation of the assignment.

A local date requires the complete timestamp/timezone/offset/interpretation tuple. `temporal_precision=second` was added to the existing enum; temporal assignment remains a separate field.

### `quality_assessments` additions

`public_grade` and `public_explanation` are an all-or-none pair. The USGS slice stores eight named quality dimensions in `findings` and uses the pair for the public result.

### `publication_manifests` addition

`editorial_revision` is a positive integer independent of profile version and publication status.

### New and changed constraints

`source_releases(source_id, raw_checksum_sha256)` is unique for idempotent imports. Raw records are immutable. Claim hashes and raw checksums must be lowercase SHA-256. Stable profile locations are version-addressed and protected by the existing published-manifest/day-profile immutability triggers.

## Senior Takeover Schema Additions

### `source_release_licenses`

One immutable license snapshot per source release. It stores the identifier,
verbatim policy snapshot, terms URL, commercial-use, redistribution,
derivatives, attribution, public-display and raw-download permissions,
attribution text, terms-check date and legal-review status. The release foreign
key is the primary key. Update and delete are rejected.

### `claim_review_decisions`

Append-only accept/reject/defer decisions for imported claims. Important
columns are claim, decision, rationale, reviewer identity and decision time.
Rows cannot be updated or deleted. Claim status is advanced through governance
services that create this record.

### `editorial_selections`

Append-only selection decisions for exactly one resolved claim or derived value
in one date/section. It stores a monotonically increasing `decision_version`,
status, display rank, rationale and reviewer. Publication eligibility uses the
highest decision version for each date/section/root combination; a selection in
one section cannot authorize use in another. Later selection, rejection or
deferral never overwrites the earlier decision. Partial unique indexes enforce
one row per root and version; service writers serialize each root with a
transaction-scoped PostgreSQL advisory lock.

### Temporal assignment addition

`period_context` identifies an aggregate that describes a period without
allocating it to individual dates. It is distinct from `direct_record`,
`uniform_period_allocation`, `modeled_period_allocation` and
`editorial_context`.

### Current structured metric records

UN WPP review materializes five metrics, annual observations and coverage for
every World aggregate year from 1950 through 2025. Rows through 2023 use
`estimated`; the 2024 and 2025 medium-variant rows use `modeled`. Average daily
births and deaths are separate derived metrics with
`uniform_period_allocation`. UCDP review materializes an
active state-based-conflict count derived from 25 resolved conflict-year inputs
using `period_context`. UCDP GED fatalities are stored in `event_impacts` with
directness `direct`; low and high bounds remain on the source claim and resolved
value rather than being collapsed into the event row.
