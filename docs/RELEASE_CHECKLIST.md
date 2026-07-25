# MVP Release Checklist

Status date: 2026-07-24

Overall status: **not MVP complete; release blocked**

## Green Technical Gates

- [x] Repository installs from pinned JavaScript and Python lockfiles
- [x] PostgreSQL/PostGIS starts locally
- [x] Database migrates from zero through `20260724_0011`
- [x] USGS fixture ingests, reviews, resolves and publishes
- [x] UN WPP fixture ingests all supported years and publishes annual demographic context
- [x] UCDP annual and GED fixtures ingest without network-dependent tests
- [x] Wikidata fixture creates candidates without automatic acceptance
- [x] Missing casualties remain absent rather than zero
- [x] Annual daily equivalents are visibly labeled as annual equivalents
- [x] UCDP conflict-year context is visibly not date-specific
- [x] Published objects are content-hashed and versioned
- [x] Corrections require a new superseding manifest
- [x] Failed validation creates no source release or publication
- [x] Source release licenses are stored and publication-gated
- [x] Development review decisions use an immutable ledger
- [x] Production frontend build succeeds
- [x] Mocked and real full-stack browser tests pass
- [x] JavaScript production dependency audit has no known vulnerabilities
- [x] Python dependency audit has no known third-party vulnerabilities

## Red Product Gates

- [ ] Golden 100 profiles generated
- [ ] Golden 100 profiles automatically validated
- [ ] Golden 100 manually reviewed
- [ ] At least one published profile in each supported era
- [x] UN WPP full supported-year pipeline
- [ ] UCDP full supported-year pipeline and revision processing
- [ ] Wikidata/Wikimedia people, organizations, births, deaths and merge review
- [ ] Curated apocalypse catalog
- [ ] Wonder and progress catalog
- [ ] Period comparison model with frozen cohort and model card
- [ ] Source disagreement represented on a public profile
- [ ] Duplicate-event, entity-merge and geography review workflows completed
- [ ] Public date previous/next navigation and canonical share metadata
- [ ] Automated accessibility audit

## Red Operational Gates

- [ ] Production authentication and authorization
- [ ] Human licensing review
- [ ] Deployment environment
- [ ] Production object-storage adapter
- [ ] Structured centralized logs and alerts
- [ ] Backup/restore drill
- [ ] Container and operating-system vulnerability scan
- [ ] GitHub Actions pinned by commit SHA
- [ ] Penetration test
- [ ] Incident response ownership

## Golden Set Status

`data/golden-set/golden-dates-v1.json` contains exactly 100 unique candidates
with every profile era and required stress category represented. Automated
validation passes selection shape and coverage. All 100 records are
`pending_human_review` and `not_generated`; this gate is intentionally red.

## Current Published Coverage

Only `1964-03-27` is published. It is a standard statistical profile containing
the reviewed USGS earthquake, UN WPP annual demographic context, UCDP annual
conflict context, evidence notes, and explicit unavailable states. This is not
the contracted MVP coverage.
