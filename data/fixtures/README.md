# Test-only fixtures

Everything in this directory is synthetic development/test input, not a
historical source and not publishable evidence. The seed command refuses to run
unless called with `--confirm-test-fixtures`.

The fixture deliberately creates only a source, an immutable source release,
and a methodology. It creates no events, day profiles, observations, or claims
that could be mistaken for production historical facts.

`test_only_raw_source_release.txt` is the synthetic raw artifact named by the
release fixture. Its SHA-256 is stored in `test_only_seed.json` so the fixture
also exercises raw-artifact checksum provenance without asserting a fact.
