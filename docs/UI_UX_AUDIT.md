# UI/UX Audit — Ground Truth

Date: 2026-07-24. Basis: direct code reading at main `9a8f288` plus the
published golden artifact. Per the working agreement (`CLAUDE.md` §1), every
claim below cites the code that makes it true; claims are classified
**confirmed / contradicted / could-not-determine**; contradicted prior
claims lead.

Scope: the public web experience (`apps/web`). The admin review console
(`apps/web/app/admin/review/page.tsx`) is out of the redesign's scope; note
that its **page route is publicly reachable** — the
`X-Development-Review-Token` gates only the `/api/admin/*` data requests
and actions the page makes, not the route itself.

---

## 1. Prior claims this audit contradicts

1. **"The frontend has no dead code" — CONTRADICTED.**
   `ProfileSections` accepts and renders a `sourceAttribution` prop
   (`apps/web/src/components/ProfileSections.tsx:19,26,30-38`), but no
   caller ever passes it: the only `<ProfileSections>` call sites are in
   `DayProfileClient.tsx` (`:99,118,144,151-155,166`) and none supplies the
   prop. The attribution `<aside>` is unreachable in the live app while the
   published artifact carries a real `source_attribution` object
   (verified in `.local/published-profiles/day/1964-03-27/profile-v1.json`).

2. **"HIGH frontend collision risk across three active branches" —
   CONTRADICTED.** The branch tips in question (`3780dcb`, `ef72488`) are
   ancestors of `origin/main` (verified with
   `git merge-base --is-ancestor`); they were stale refs of merged PRs, not
   in-flight work.

3. **"The UI distinguishes recorded facts, annual context, derivation, and
   absence" (docs/HANDOFF integrity check) — CONFIRMED ONLY AS PROSE,
   overstated as UI.** The distinctions exist as sentence wording composed
   by the backend (e.g. "average daily equivalent based on the annual
   total, not an observation for March 27", pinned in
   `apps/web/e2e/full-stack-golden.spec.ts:22-26`) and as section
   membership. In the collapsed reading view, nothing encodes statement
   class: every statement is an identical
   `<article class="profile-statement">` (`ProfileSections.tsx:53`)
   regardless of provenance root. The class IS named — "Resolved claim"
   vs "Derived value" (`ProfileSections.tsx:67-85`) — but only inside
   each statement's expanded provenance disclosure, one `<details>` at a
   time. A reader who skims statement text sees typography that treats
   "magnitude 9.2" and "about 320,470 average daily births" as the same
   kind of assertion.

---

## 2. Current state (verified)

### Routes and composition

| Route | File | What renders |
| --- | --- | --- |
| `/` | `apps/web/app/page.tsx` (17 lines) | Masthead (eyebrow, h1, lede) + `DateInputForm`. |
| `/day/[date]` | `apps/web/app/day/[date]/page.tsx` | Masthead with `h1 "Day profile: {date}"`, same `DateInputForm` (`:20`), then client `DayProfileClient` (`:23`). Server page does no validation or redirect. |
| `/api/day/[date]` | `apps/web/app/api/day/[date]/route.ts` | Proxy to FastAPI `API_BASE_URL`, `Cache-Control: no-store`, network failure → 503 `api_unavailable`. |
| `/admin/review` + `/api/admin/[...path]` | dev-only console + proxy | Out of scope. |

`app/layout.tsx` exports a **static** metadata object (title "Day
Perspective") for every route — there is no per-date `generateMetadata`,
so shared links all preview identically.

### Client state machine

`DayProfileClient.tsx` holds a four-state view model
(`loading | unpublished | api-error | published`, `:12-16`) with correct
async hygiene: `AbortController` on date change (`:44,83-85`), stale-state
guard (`:88-89` — a new `date` prop immediately renders `loading`), retry
via `requestNumber` (`:133-142`), and strict payload validation before
render (`src/lib/day-profile.ts:154-192`) so malformed payloads land in
`api-error`, never a crash. Invalid dates short-circuit with no fetch
(`:40-42,91-102`).

### Data the frontend genuinely receives (artifact-verified)

The golden profile (`1964-03-27`, the only published date) contains 16
statements: 8 `recorded_on_this_date` (all `root_type: resolved_claim`),
2 `typical_day_in_this_year` (both `derived_value`, `details` carrying
`temporal_assignment: uniform_period_allocation`-class markers,
`data_status`, `days_in_year`, `interpretation`), 4
`wider_historical_context` (3 resolved claims with
`temporal_assignment`/`temporal_precision`/`data_status`, 1 derived value
with `missing_data_explanation`), 1 `evidence_notes` (derived value with
`quality_grade`, `missing_data`). Top level: `quality` (grade B plus a
five-line explanation), `source_attribution`, and `section_states` marking
`curated_claims`, `derived_comparisons`, `wonder_and_progress` as
`not_yet_supported` with reasons. `dissenting_claims` is empty for every
statement — disputed display has zero real data instances today.

The class markers in `details` are **untyped** — the contract declares
`details?: Record<string, unknown>` (`packages/contracts/src/index.ts:25`).
The reliable typed signals of statement class are the section key plus the
**validated presence** of a `resolved_claim` vs `derived_value` object:
the payload validator requires at least one of the two typed branches
(`day-profile.ts:78-103`, disjunction at `:103`) but never checks
`root_type`, which the contract declares optional
(`packages/contracts/src/index.ts:27`). `root_type` may corroborate but
must not be the discriminant.

### Styling

One global stylesheet, `apps/web/app/globals.css` (339 lines): design
tokens as custom properties (`:1-10` — paper `#f7f2e8`, ink `#1e2a33`,
accent `#aa432d`, focus `#074f57`), Georgia serif body + Courier
monospace eyebrows/labels, 72rem page shell (`:42-46`), a two-column
equal-card `section-grid` with the seventh card spanning both columns
(`:176-188`), one mobile breakpoint at 42rem (`:222-239`) that stacks the
form and collapses the grid, a 1.3s opacity `pulse` keyframe for loading
lines (`:199-220`), and admin styles (`:241-312`). No Tailwind, no CSS
modules, no animation/UI dependencies (`apps/web/package.json`).

### Loading, empty, and error behavior

- Route-level `loading.tsx` (root and day) render pulse skeletons with
  `aria-busy`/`aria-live`.
- In-client loading, unpublished, api-error, and invalid-date panels each
  render an explicit `state-panel` plus the seven section cards in the
  matching availability mode (`DayProfileClient.tsx:91-168`); per-section
  fallback copy at `ProfileSections.tsx:8-13,120-127`.
- Published-but-empty sections show `section_states[key].reason` when
  present, else a generic sentence (`ProfileSections.tsx:122-126`).
- Error boundaries at both levels (`app/error.tsx`,
  `app/day/[date]/error.tsx`) with reset buttons and copy that explicitly
  refuses to imply publication status.

### Accessibility state

Present and verified: labeled native date input with `min`/`max` and
`aria-describedby`-wired `role="alert"` error (`DateInputForm.tsx:39-58`);
`:focus-visible` 3px outline (`globals.css:37-40`); `aria-labelledby` on
sections and panels; `aria-busy`/`aria-live` on loading; native
`<details>/<summary>` disclosure and `<dl>` provenance semantics
(`ProfileSections.tsx:64-116`); `html lang="en"`.

Gaps: **zero `prefers-reduced-motion` handling anywhere in `apps/web`**
(grep-verified) while the pulse animation runs infinitely; no skip link;
no `header`/`nav` landmarks (the section cards and state panels ARE
exposed as named region landmarks via `aria-labelledby`,
`ProfileSections.tsx:46`, `DayProfileClient.tsx:94,107-110,126` — the
missing landmarks are banner and navigation, not regions); no automated
a11y scanning in any test.

### Tests

- vitest/RTL: `DayProfileClient.test.tsx` (5 cases: unpublished honesty,
  validated publish render, provenance open, date-change reset,
  unrecognized-response rejection), `ProfileSections.test.tsx`
  (supporting-link labeling), `AdminReviewPage.test.tsx` (2),
  `day-profile.test.ts` (validators). 10 web tests total, run-verified.
- Playwright (`playwright.config.ts:11-18`): **desktop-chromium only**; no
  mobile viewport, no reduced-motion context. `webServer` runs
  `next build && next start` (`:19-26`), so every e2e invocation rebuilds.
- e2e specs: `golden-profile.spec.ts` (mocked publish + provenance chain),
  `unpublished-state.spec.ts`, `full-stack-golden.spec.ts` (self-skipped
  unless `DAY_PERSPECTIVE_FULL_STACK=1`, `:6-9`).
- **The landing page has zero test coverage** — no spec visits `/`.

### Functional strengths (keep these)

1. Honest states are load-bearing copy, not chrome: "No historical facts
   are substituted for the missing profile." (`DayProfileClient.tsx:113-116`).
2. Validation-before-render with typed narrowing (`day-profile.ts:106-192`).
3. Clean seams: the server page composes `DateInputForm` and
   `DayProfileClient` as siblings; `ProfileSections` is purely
   presentational; contracts package is the single shared type source.
4. The provenance chain largely renders already: resolved claim/derived
   value, supporting claims with source-record links, source release,
   methodology (`ProfileSections.tsx:63-117`). One material gap:
   **dissenting claims render as a retained count only**
   (`ProfileSections.tsx:99-104`) — no predicates, locators, or links —
   so any future dispute would be uninspectable. The redesign must give
   dissent the same completeness as support (slice B3).
5. Zero heavy dependencies; pure CSS with tokens; native form semantics.

---

## 3. Contract conflicts and weaknesses

Each item names the product rule it strains and the evidence.

1. **Equal visual weight for unequal statement kinds** (violates the
   spirit of `docs/PRODUCT_CONTRACT.md:37-49`, "The interface must keep
   these categories visibly separate"). Seven identical numbered cards
   (`ProfileSections.tsx:46-50`; grid `globals.css:176-188`); identical
   statement typography regardless of evidence class (§1.3 above). The
   class distinction survives only in backend-composed prose and section
   headings. A quiet date and 1964-03-27 produce the same layout with
   different amounts of emptiness.

2. **Required provenance surface dropped by the UI**
   (`docs/PRODUCT_CONTRACT.md:71-78` requires manifest content hash and
   lineage inspectability). The validator *requires* `manifest_id` and
   `content_hash` (`day-profile.ts:165-166`) and the client then discards
   them (`DayProfileClient.tsx:70-73`). Top-level `quality` and
   `source_attribution` are never rendered (§1.1; no caller passes the
   prop; no quality prop exists at all).

3. **No date navigation** (mission flow 5; also
   `docs/RELEASE_CHECKLIST.md` red gate "Public date navigation
   (previous/next) and canonical share metadata"). No prev/next/random
   affordance exists; the only way to move is re-typing in the form. No
   `generateMetadata`; static share preview for all dates
   (`app/layout.tsx`).

4. **No canonical URL handling.** `/day/1964-3-27` (real date, unpadded)
   renders the invalid-date panel whose copy — "Choose a date from
   1900-01-01 through 2025-12-31." (`DayProfileClient.tsx:96`) — misleads:
   the date IS in range; the format is what failed
   (`date.ts:6` regex gate). No redirect to the padded canonical form.

5. **Reduced-motion violation.** Infinite pulse animation with no
   `prefers-reduced-motion` guard (`globals.css:199-220`; grep confirms no
   handling anywhere in `apps/web`), including both route `loading.tsx`
   files.

6. **Empty-state variants are textually but not structurally distinct.**
   `not_yet_supported`-with-reason vs published-but-empty vs unpublished
   all render as one sentence inside an identical card
   (`ProfileSections.tsx:120-127`); the artifact's honest
   `section_states` reasons ("This vertical slice does not publish this
   evidence class.") deserve structural presentation.

7. **Arrival is generic.** `h1 "Day profile: {date}"` with an identical
   lede for every date (`app/day/[date]/page.tsx:14-19`); no era/band
   framing (the typed `profileTypeForDate` band is computed for validation
   but never shown to the reader), no world-scale context, no sense of
   having gone anywhere.

8. **e2e selector fragility that a redesign will trip.**
   `full-stack-golden.spec.ts:33-35` walks `.locator("..")` from a
   heading to find its card, which breaks on any DOM restructure.
   `golden-profile.spec.ts:70-76` and `DayProfileClient.test.tsx` pin the
   summary copy "Why can the app say this?", the link name pattern
   "the {source} source record", and "None in this publication."
   (A prior draft claimed a "9.2 MW."-vs-"9.2 Mw." casing mismatch here;
   that was false — the pipeline uppercases the scale (`usgs.py:1236`)
   and the published artifact reads "9.2 MW.", matching the pin exactly.
   The lowercase "Mw." appears only in `golden-profile.spec.ts:22`'s own
   mock payload, which is self-consistent. Correction retained per the
   audit's own discipline.)

9. **Single-breakpoint responsiveness.** One 42rem media query
   (`globals.css:222-239`). Nothing between phone and 72rem desktop; no
   mobile viewport in the test matrix (`playwright.config.ts:11-18`).

10. **Disputed data has no real instance** — every artifact statement has
    zero dissenting claims (artifact-verified), so the dissent renderer
    (`ProfileSections.tsx:99-104`) has never displayed a real dispute.
    Any disputed-display design must be mock-tested and must not imply
    disputes exist for 1964-03-27.

Could-not-determine: whether GitHub branch protection marks CI required
(API access to protection settings unavailable in this environment);
whether the backend can cheaply expose a published-dates index (needs
backend-agent coordination — logged as an issue, not assumed).

---

## 4. Opportunity

What exists is an honest ledger. What is missing is the experience the
product exists to create: the sensation of *leaving the present and
standing inside a particular day* — its scale, its recorded violence and
wonder, its ordinary simultaneity — without the ledger's honesty ever
loosening.

The user should **feel** arrival: the date as a place, not a query result;
a first screen that says *when you are* (date, era band, the world's scale
that year) before it says anything else. They should **understand**
immediately which statements are records of that day, which are averages
spread over a year, which describe the surrounding period, and which are
the app's own comparisons — by structure and label, not by parsing
sentence qualifiers. They should be able to **do** three things without
friction: move (previous day, next day, another date, a random day),
interrogate (open any statement's evidence and see how far the app's
knowledge actually reaches, including its gaps), and share (a canonical
URL whose preview names the date).

The absence of data is part of the material. `not_yet_supported` sections,
missing values with explanations, and unpublished dates should read as an
honest map's blank regions — labeled terra incognita — not as a broken
dashboard. The current codebase is unusually well-positioned for this:
states are already explicit, provenance is already complete, seams are
already clean. The redesign's work is hierarchy, movement, and visible
epistemics — not plumbing.
