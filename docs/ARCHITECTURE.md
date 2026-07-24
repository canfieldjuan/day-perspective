# Architecture

## Principle

The system resolves evidence before it renders a historical profile. PostgreSQL
is authoritative for structured provenance and resolution. Published JSON is a
versioned delivery artifact, not untracked truth.

```text
raw artifact -> normalized claims -> resolved claims -> derived values
     -> editorial selection -> published JSON and manifest -> API -> web
```

## Data layers

| Layer | Responsibility | Core records |
| --- | --- | --- |
| Raw | Preserve received source material and acquisition identity. | Raw artifact, checksum, source, source release. |
| Normalized | Turn raw records into source assertions without accepting them as truth. | Claims, observations, source lineage. |
| Resolved | Assess evidence and materialize canonical claims, entities, events, and impacts. | Resolved claims, evidence links, event dates/locations, geography versions. |
| Derived | Calculate daily equivalents and comparisons from versioned methods and durable inputs. | Methodologies, derived values, input links, coverage, quality. |
| Published | Build immutable profile JSON, statement-evidence links, and publication metadata. | Manifests, publication statement evidence, day profiles, corrections. |

A `source_release` captures an immutable raw snapshot with a SHA-256 checksum.
Every claim requires a release and a raw record locator. Resolved claims retain
at least one supporting imported claim and any explicit dissenting imported
claims. Events are materialized from
resolved claims, can have many dates and locations, and store impacts only in
`event_impacts`. Geography identity is separated from historical versions so a
location can use the appropriate historical name/boundary/geometry.

## Components and boundaries

| Component | Does | Does not do |
| --- | --- | --- |
| `apps/web` | Next.js date input and separate profile sections; loading, unpublished, and API-error states. | Resolve claims, ingest sources, or call third parties. |
| `services/api` | FastAPI read endpoints, Pydantic validation, SQLAlchemy/Alembic, offline pipeline commands, and profile storage interface. | Invent facts or fan out to live sources during a page request. |
| `packages/contracts` | Shared TypeScript API/profile contracts. | Own database authority. |
| PostgreSQL with PostGIS | Source/provenance, resolution, quality, workflow, and publication metadata. PostGIS supports historical geometry. | Serve as an unversioned public blob store. |
| Published-profile storage | Immutable JSON by storage key; local filesystem implementation for development. | Become the source of truth for claims. |
| `data/fixtures` | Explicitly test-only inputs. | Populate a production fact catalog. |

## Published JSON storage

The API uses a small storage interface: write a pre-publication artifact, read
by immutable storage key, and verify a content hash. The manifest owns the
storage key and SHA-256 hash. Every profile statement has an immutable
`publication_statement_evidence` row that points to one resolved claim or
derived value and captures canonical JSON plus a SHA-256 hash of that root's
complete evidence state. The manifest source-snapshot hash is calculated from
the ordered statement paths and evidence hashes; callers cannot supply it.
This permits working claims to evolve while retaining exactly what a published
version asserted. The first implementation is local filesystem
storage configured by environment variable. It is deliberately not a production
durability, access-control, backup, or concurrent-writer design. Any later
object-store implementation must preserve the key/hash/manifest contract.

## Offline-only ingestion

All third-party acquisition occurs in an explicit offline pipeline:

```text
acquire artifact -> checksum/register release -> retain raw locator
-> normalize claims/observations -> quality checks and review tasks
-> resolve -> derive -> snapshot and map every profile statement to evidence
-> write profile JSON -> hash/publish manifest
```

Pipelines record a run, code version, methodology, inputs, and quality results.
Republished, translated, compiled, corrected, and derived releases use
`source_lineage`; they are not disguised as independent original sources. A
pipeline may leave a date unpublished. It must not substitute a vendor request
or fabricated content.

## Runtime request path

```text
reader chooses ISO date in Next.js
-> web calls this FastAPI service only
-> API loads profile/manifest metadata from PostgreSQL
-> API reads immutable JSON through storage interface
-> API returns validated profile or profile_not_published
-> web renders distinct evidence sections and notes
```

The API skeleton exposes `GET /health`, `GET /api/v1/system/status`,
`GET /api/v1/methodologies`, `GET /api/v1/sources`, and
`GET /api/v1/day/{yyyy-mm-dd}`. The day endpoint returns HTTP 404 with `{status, date, profile_type, detail}` where `status` is
`profile_not_published` until a matching published artifact exists.

Third-party data is never queried during ordinary page rendering because live
calls make sources nondeterministic, undermine provenance and reproducibility,
introduce availability/licensing drift, and can silently change history after
publication.
