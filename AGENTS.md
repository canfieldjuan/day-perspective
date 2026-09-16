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
   issue before landing the PR. A post-round-3 finding that the PR asserts
   something false is neither, and deferring it would ship a known-wrong
   statement. Where the false assertion is prose, remove it rather than
   rewriting it, and take its dependents with it: check what points at the
   clause first, because one that defines a term or is cross-referenced
   cannot be lifted alone and a dangling reference is a new defect. If the
   dependents will not lift out cleanly, split.
   Where it is executable — a test assertion, a validation branch, a data
   mapping — deleting it drops coverage or a guard and lets the defect
   land, so never delete a test or a guard to converge: split scope so the
   false part leaves this PR and gets its own slice, with its own review
   budget. Either way, split scope if what remains has no point, say what
   was removed or split out and why in the PR and to the operator, and log
   the gap as an issue. After round 3 the author does not make this call
   silently.

Everything else — ground-truth citation discipline, contracts-first, TDD,
vertical slices, merge policy, document ownership, honest-data rules — is
in `CLAUDE.md`.
