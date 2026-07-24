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
