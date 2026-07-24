# Backup and Restore

No production backup system exists. This document defines the local recovery
procedure and the production requirements that remain release blockers.

## Authoritative State

PostgreSQL is authoritative for claims, review, resolution, methodologies,
corrections, and publication manifests. Raw-source objects and published JSON
are immutable filesystem objects in development. A usable backup must contain
all three stores from one coordinated recovery point.

## Local Backup

```bash
mkdir -p .local/backups
docker compose exec -T db pg_dump \
  -U day_perspective \
  -d day_perspective \
  --format=custom > .local/backups/day-perspective.dump
tar -C .local -czf .local/backups/objects.tar.gz \
  raw-sources published-profiles
sha256sum .local/backups/day-perspective.dump \
  .local/backups/objects.tar.gz > .local/backups/SHA256SUMS
```

## Local Restore

```bash
sha256sum -c .local/backups/SHA256SUMS
make clean-reset
make db-up
make db-migrate
docker compose exec -T db pg_restore \
  -U day_perspective \
  -d day_perspective \
  --clean --if-exists < .local/backups/day-perspective.dump
tar -C .local -xzf .local/backups/objects.tar.gz
```

After restore, run:

```bash
make check
make web-e2e-full-stack
```

## Production Requirements

- Encrypted database and object backups
- Coordinated recovery-point identifiers across all stores
- Retention and deletion policy
- Off-site copies and access logging
- Automated checksum verification
- Quarterly restore drills with measured recovery time and recovery point
- Documented key recovery and incident ownership

The commands above have not yet been exercised as a full restore drill.
