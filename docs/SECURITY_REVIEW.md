# Security Review

Review date: 2026-07-24

## Scope

This review covers the local API, Next.js application, dependency manifests,
development review guard, local publication storage, fixture pipelines, and CI.
It is not a penetration test and does not approve production deployment.

## Verified Controls

- Ordinary public requests read immutable published artifacts and do not contact
  third-party data sources.
- Public date input is validated at the web proxy and API boundaries.
- Publication objects and raw-source files are constrained to configured roots.
- Published objects and source releases are content-hashed.
- SQLAlchemy is used for database queries; no user-provided SQL is constructed.
- The admin proxy forwards only the explicit development token and JSON body.
- Claim decisions use the append-only review ledger.
- Candidate claims cannot be resolved by the admin resolution endpoint until
  they have been explicitly accepted.
- `pnpm audit --prod` reported no known vulnerabilities after the Next,
  Playwright, Sharp, and PostCSS upgrades.
- `pip-audit` reported no known vulnerabilities in resolved third-party Python
  packages after the FastAPI, Starlette, and pytest upgrades.

## Release Blockers

- The development review token is shared-secret access, not user
  authentication, authorization, session management, or audit identity.
- CORS permits the configured development web origin but has not been reviewed
  for a production origin.
- No rate limiting, abuse controls, security headers policy, secret rotation,
  centralized audit log, or production TLS termination is implemented.
- CI actions are version-tag pinned, not commit-SHA pinned.
- The Docker image and operating-system packages have not been vulnerability
  scanned.
- Source-license records have not received human legal review.
- There is no production backup encryption, retention, or restore drill.
- No production deployment exists.

## Threat Notes

The highest-risk current surface is the development review API because it can
accept or reject claims and publish reviewed content. It must not be exposed on
the public internet. The public API is read-only, but corrupt storage, a leaked
database credential, or a compromised publication process could still affect
integrity. Content hashes detect artifact changes; they do not prevent a
privileged attacker from changing both database and storage.

## Required Production Follow-up

1. Replace the development guard with production authentication and
   role-based authorization.
2. Pin GitHub Actions by immutable commit SHA.
3. Add rate limiting, security headers, TLS and secret-management design.
4. Run container, dependency, SAST, and DAST checks in CI.
5. Complete a human licensing review.
6. Perform and record a backup/restore drill.
7. Commission a focused penetration test before public launch.
