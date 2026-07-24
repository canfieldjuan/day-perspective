# Day Perspective API

This service owns the claim-resolution data model and the read-only public API.

Use the repository-level README and Makefile for normal setup. The service
implements only the foundation endpoints and reads published JSON through the
local development storage interface. Run `make api-migrate`, `make api-seed`,
and `make api-run` from the repository root.

`app.fixtures` accepts only `data/fixtures/test_only_seed.json`, whose
`TEST_FIXTURE_ONLY_NOT_HISTORICAL_EVIDENCE` marker is checked before seeding.
It also refuses to run unless `DAY_PERSPECTIVE_ALLOW_TEST_FIXTURES=1`; the
repository `make api-seed` target sets that opt-in. It creates no historical
event, observation, claim, or profile content.
