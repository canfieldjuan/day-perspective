# Deployment State

There is no production deployment. The repository is locally runnable and has
CI configuration, but production release is blocked.

## Current Runtime Shape

- Next.js 16 web application
- FastAPI 0.139 API
- PostgreSQL 16 with PostGIS 3.5
- Immutable raw-source and publication object stores
- Offline pipeline and review commands

## Required Production Topology

The web service may call only the internal API. Public day requests must read
published objects and verify manifest hashes. Pipeline workers must run outside
ordinary request handling and must have separate credentials. The review
surface must be private and protected by production authentication.

## Schema Upgrade Steps

`20260726_0014` creates and backfills the coverage index.
`20260727_0016` adds review status and evidence quality with conservative
defaults of `automated_only` and `not_assessed` for existing rows. It leaves
their actual values to the coverage rebuild, which uses the same derivation
as publication.

After upgrading a populated archive, run the coverage rebuild before
resuming reader traffic. This is a required upgrade step. Without it,
existing pages can continue showing the migration defaults despite having
review decisions or graded evidence.

```bash
make rebuild-coverage
```

Require `dropped=0` and `unreadable=0` before resuming reader traffic.
Investigate either nonzero count. The rebuild verifies each published
artifact and derives its coverage metadata without republishing content.
It is idempotent and reports what it indexed and dropped. An unreadable
artifact is excluded from navigation and causes a nonzero exit status.
An interrupted rebuild can be rerun to finish the remaining dates.

## Blocking Decisions

- Hosting provider and region
- Managed PostgreSQL/PostGIS provider
- Durable object-storage implementation
- Secret manager and identity provider
- Backup, monitoring, alerting and incident ownership
- Domain, TLS, cache and rollback policy
- Human legal review for source releases

## Pre-Deployment Gate

Do not deploy publicly until every blocking item in
`docs/RELEASE_CHECKLIST.md` is green. In particular, do not expose
`/api/v1/admin/*` with the development review token.
