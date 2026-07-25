# Day Perspective — Working Agreement

This file is the shared working agreement for every agent and human working
in this repository (Claude sessions, Codex sessions, human reviewers).
`AGENTS.md` carries a verbatim excerpt of the reviewer-critical rules for
tools that read only that file; when editing either file, keep the excerpt
in sync. If they drift, this file wins.

Product rules live in `docs/PRODUCT_CONTRACT.md` and are not restated here.
Commands live in the `Makefile` (`make help`); it is the command source of
truth.

## 1. Ground truth is the code

Docs, comments, PR descriptions, commit messages, review threads, and prior
findings are unverified claims. Before asserting what the system does, read
the code that does it. Audits, reviews, and findings cite `file:line` for
every claim, classify each claim as confirmed / contradicted /
could-not-determine, and lead with claims that turned out wrong or
overstated. Never report a claim as confirmed without a citation.

Why: two pre-arc audits of this repo contained materially false claims (a
"HIGH collision risk" between branches that were already merged ancestors of
main; "no dead code" while `ProfileSections`' source-attribution render path
had no live caller). Direct code reading caught both.

## 2. Contracts first

`packages/contracts/src/index.ts` and `docs/PRODUCT_CONTRACT.md` bind the
UI and the API. Derive and check the contract before writing code; do not
violate a contract to simplify a screen. Changing a contract is its own
deliberate, reviewed PR — never a side effect of a feature PR.

Why: the product's entire value is evidential honesty; contract drift
destroys it silently.

## 3. TDD

Write the failing test first: vitest/RTL for component behavior, Playwright
for user flows, pytest for the API. Test behavior and meaning — labels,
states, semantics — never decorative animation frames. No `waitForTimeout`
in specs.

## 4. Vertical slices

Prefer a thin end-to-end slice (data → API → UI → test) over a horizontal
layer. A slice PR ships an observable improvement, not scaffolding for a
future one.

## 5. Tightly-scoped PRs

One concern per PR — one route, one component-cluster, or one document set.
Net app-code diff above ~800 lines (excluding lockfiles, fixtures, and
generated files) means split before opening.

Why: PR #5 accumulated 9 review threads across 4 review rounds; scope is
the only lever that bounds review surface.

## 6. Review loop

After opening a PR, wait for the reviewer (Codex) to file a review. Address
EVERY thread — push a fix, or reply with reasoned evidence — before
resolving anything. Only an addressed thread gets resolved. Then wait for
the next round. Post a "round N addressed" comment per round so the round
count is auditable.

Never resolve a thread you have not addressed. Never merge with unresolved
threads.

## 7. Three-round convergence rule

If review has not converged after 3 rounds, stop pushing fixes and step
back. Is the PR scope too broad? Split it. Are the remaining findings
edge-case-only? Defer them: log each in the slice's GitHub issue, then land
the PR.

Why: infinite thread-chasing costs more than a logged deferral (precedent:
issue #4).

## 8. Merge on green + converged

When every required check is green and all review threads are resolved,
merge (squash), alert the operator, and continue. Do not idle waiting for a
human click. Never merge with a red required check or an unresolved thread.

## 9. No dead code

A PR that replaces UI removes what it supersedes — components, styles,
tests — in the same PR. No orphaned render paths, no "kept just in case".

## 10. Issue tracking

Long arcs get an epic issue plus one issue per slice carrying acceptance
criteria. Deferred findings go to issues, not memory or chat. Branches are
named `agent/<slice-name>`.

## 11. Document ownership

- `docs/STATUS.md`, `docs/HANDOFF.md`: live working documents of the active
  backend/pipeline agent. Other arcs write them only at arc close, and
  additively.
- `docs/UI_UX_AUDIT.md`, `docs/UI_UX_DIRECTIONS.md`,
  `docs/UI_UX_CONTRACT.md`: owned by the UI arc.
- `docs/DECISIONS.md`: append-only D-entries; anyone may append, nobody
  rewrites history.
- Once merged, `docs/UI_UX_CONTRACT.md` is the design referee: a review
  thread that disputes taste on an implementation PR is answered
  "per UI_UX_CONTRACT §C-n" and resolved, or becomes a contract-amendment
  PR. Design taste is not litigated per-PR.

## 12. Honest data

Statement and provenance text render only from API/props data. A date-shaped
or numeric historical literal in `apps/web` JSX is a review blocker.
Unavailable data is never rendered as zero. Fixtures are development- and
test-only and never masquerade as production facts.

## Practicalities

- `gh` auth: an invalid `GH_TOKEN` env var can shadow working keyring auth;
  if `gh` returns 401, retry as `env -u GH_TOKEN gh …`.
- Verify locally before pushing: `make check` on every push, `make verify`
  before opening a PR. `make verify` is not self-contained: it needs a
  running migrated database with the golden fixture published and
  Playwright browsers installed first — from a fresh checkout run
  `make db-up && make db-migrate`, then the fixture ingest/review cycle
  ending in `make publish-golden` (exact order in `README.md` and
  `.github/workflows/ci.yml`), then `make verify`. CI runs the same
  pipeline (~30 minutes) on every PR — local verification is cheaper
  than CI discovery.
- e2e selectors: role + accessible name for user-facing assertions,
  `data-testid` for structural locators. Once `docs/UI_UX_CONTRACT.md`
  exists and merges (a UI-arc deliverable), canonical user-facing strings
  live there and changing one is a contract amendment, not a per-PR
  debate. Until then, the strings asserted by the existing e2e and
  component tests are the de facto canon; change one only with an
  explicit callout in the PR description.
