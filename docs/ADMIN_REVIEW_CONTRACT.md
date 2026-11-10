# Selected-date candidate review

This contract governs the bounded extension of `/admin/review` tracked in
#159 under #157, with exact Wikidata workflow targeting tracked in #108.
It extends the local development console; it does not authorize any
historical claim or publication. Public reading remains governed by
`PRODUCT_CONTRACT.md` and `UI_UX_CONTRACT.md`.

## Operator flow

1. Enter the development review token and choose a supported public date.
   Load already imported Wikidata candidates for that qualified occurrence
   day. Loading is read-only. An empty result means no matching imported
   candidate, not that nothing happened on the date.
2. Choose an immutable source release. Show its entity identifier, release
   identifier, revision information as recorded, source URL and checksum.
   Multiple candidates on a date remain separately identifiable.
3. Inspect each claim's predicate, complete asserted value, review status,
   raw-record locator and recorded reference information. Missing references
   remain explicit. A Wikipedia import is not independent corroboration;
   acceptance does not upgrade evidence quality or the source's data state.
4. Accept or reject individual reviewable claims with a nonblank rationale.
   Identity, name, type and occurrence require their own decisions. Terminal
   decisions stay immutable under the existing governance rules. Optional
   claims are visible but never accepted by selecting or publishing the core.
5. Resolve the accepted core claims, then explicitly select the resolved
   name, type and occurrence statements for publication in that order, with
   an editorial rationale. Show that ordered set before recording the
   selection, together with any existing editorial choices that it would
   change. Existing optional selections are not silently removed. Claim
   acceptance, resolution, editorial selection and publication are distinct
   actions; none silently performs the next.
6. Publish that candidate on the selected date through the existing
   Wikidata publisher. Report the actual outcome. A successful publication
   shows its manifest identifier and a link to the date's public profile.
   A collision or other blocked outcome is not a success message and does
   not bypass the existing governance workflow.

## Subject binding

The source release is the workflow subject, not the newest global import.
Use one shared release-selection owner for enrichment, resolution,
adjudication and publication, including the CLI. Each stage accepts an
explicit release identifier and checks that it belongs to Wikidata. A
selector-free invocation is compatible only when exactly one Wikidata
release exists. Otherwise refuse with the available release identifiers;
never pick by recency. The console always sends an explicit release.

Every console mutation binds the selected date and release; a claim decision
also binds the claim identifier. The server verifies this relationship
before any governance or publication write. It obtains the qualified day
from persisted occurrence evidence using the adapter's established temporal
rules, not a new string parser or a browser-supplied assertion. A missing,
unsupported or mismatched date, foreign-source release, or unrelated claim
is refused without changing another candidate.

Release selection alone is insufficient where canonical resolutions are
looked up by entity. Before reusing a resolved event or publishing that
candidate's selected roots, establish that its core resolution evidence
belongs to the selected release and agrees with the event's occurrence.
A different revision of the same entity must not silently reuse an older
resolution. If this binding
cannot be established, expose a conflict and require separate revision
reconciliation; this slice does not rewrite existing resolutions.

## State, safety and publication

- The first console path covers day-precision candidates that the existing
  adapter qualifies to one supported occurrence day. Imprecise, interval or
  otherwise unqualified candidates cannot be made publishable by the UI.
  Existing CLI temporal capabilities are preserved; #143 remains separate.
- Reuse the existing review, resolution, editorial and publication writers.
  Keep their locking, eligibility, provenance, collision adjudication,
  deterministic featuring and admitted-event retention rules. The UI does
  not infer a distinct-event decision or choose a replacement headline.
- Subject validation fails before writes. Each governance action owns its
  transaction; publication retains the publication spine's established
  recovery behavior. Do not imply that several HTTP requests are atomic.
  Collision deferral may record its existing review task without publishing.
- A publication retry follows existing content-hash idempotency. It must not
  force a new version. Failure or an uncertain network result requires a
  state refresh; it must not trigger a silent mutation retry.
- Mutations remain behind the development guard. The token stays in memory
  and is never put in a URL or persistent browser storage. This remains a
  local utility, not production authentication or a public contribution form.
- Clear prior candidate data when changing date or credentials. Ignore late
  load responses for an obsolete subject. Prevent duplicate actions and
  subject switching during a pending mutation; refresh authoritative state
  after each action, including a refused stale decision.
- Use labeled controls, keyboard-operable actions and announced outcomes.
  Keep values and source details inspectable on desktop and narrow screens.
  Do not replace an API error with a generic success or a stale preview.

## Qualification and boundaries

Implementation must prove the whole path with an offline public fixture:
load evidence, record explicit reviews, resolve, select, publish, then read
the resulting profile and immutable evidence mapping. Prove stable repeat
and preservation of another already admitted event. Boundary probes include
an unaccepted core claim, rejected optional evidence, an unknown or
foreign-source release, a wrong date or claim, multiple in-flight entities,
and a conflicting same-entity revision. Ingest A then B and operate on A
through every stage; B must remain reachable and unchanged. Include stale
decision, duplicate-action and late-load UI cases, and desktop/mobile checks.

Ship exact release targeting under #108 before the API/UI consumer depends
on it. Keep both implementation PRs bounded; this contract is reviewed
separately under `CLAUDE.md` section 2. The API/UI slice must deliver the
operator flow, not merely a new endpoint or an inactive screen.

This slice does not add source discovery or ingestion, live source queries,
new historical approvals, Golden-100 selection-file edits, clock-time
qualification, revision reconciliation, collision adjudication controls,
production authentication, public layout redesign, new sources, or an
extension of the public date range. Existing USGS and source-specific
workflows remain available; shared subject rules do not become a new
implicit acceptance path.
