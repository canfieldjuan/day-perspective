# Agent Working Agreement (excerpt)

The canonical working agreement is `CLAUDE.md` at the repository root. The
three reviewer-critical rules below are inlined verbatim for tools that read
only this file; if the two files drift, `CLAUDE.md` wins.

1. **Tightly-scoped PRs.** One concern per PR — one route, one
   component-cluster, or one document set. Net app-code diff above ~800
   lines (excluding lockfiles, fixtures, and generated files) means split
   before opening.

2. **Review loop.** After opening a PR, wait for the reviewer to file a
   review. Address EVERY thread — push a fix, or reply with reasoned
   evidence — before resolving anything. Only an addressed thread gets
   resolved. Then wait for the next round. Post a "round N addressed"
   comment per round so the round count is auditable. Never resolve a
   thread you have not addressed; never merge with unresolved threads.

3. **Three-round convergence rule.** If review has not converged after 3
   rounds, stop pushing fixes: reassess PR scope (split if too broad), and
   defer edge-case-only findings by logging them in the slice's GitHub
   issue before landing the PR. A post-round-3 finding may instead show the
   PR asserts something false; deferring that ships a known-wrong
   statement, so it is not simply an edge case to log. The author does not
   resolve it alone: say what is false and where, in the PR and to the
   operator, and let them choose the remedy. One constraint holds
   regardless — never delete a test or a guard to converge.

Everything else — ground-truth citation discipline, contracts-first, TDD,
vertical slices, merge policy, document ownership, honest-data rules — is
in `CLAUDE.md`.
