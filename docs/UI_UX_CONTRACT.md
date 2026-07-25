# UI/UX Contract — "Strata"

Binding design contract for the Time-Travel Experience arc. Chosen
direction: **Strata** (`docs/UI_UX_DIRECTIONS.md`); ground truth:
`docs/UI_UX_AUDIT.md`. Clauses are numbered **C-n** and referenced from
implementation PRs; per `CLAUDE.md` §11, a review thread disputing a
decision this contract makes is answered by citation ("per C-n") and
resolved, or becomes a contract-amendment PR. Nothing here overrides
`docs/PRODUCT_CONTRACT.md`; where they touch, the product contract wins.

## C-1 Experience principles

1. **Arrival before information.** The first thing a date page communicates
   is *when you are*: the date, its era band, its publication status. Every
   other element ranks below.
2. **Honesty is structural.** Evidence classes, gaps, and uncertainty are
   expressed by layout, labels, and typography — never only by sentence
   qualifiers, tooltips, or a methodology page.
3. **Reading is calm.** Long-form readability beats spectacle: generous
   measure (≤ 70ch), unhurried leading, no ambient motion while reading.
4. **Movement is cheap.** Changing dates is the core loop; repeat
   navigation must never wait on ceremony (C-7).
5. **Absence is material.** Unpublished dates, unsupported sections, and
   missing values render as labeled blank regions of an honest map — never
   as broken UI, never as zero.

## C-2 Visual identity

1. **Palette** extends the existing tokens (paper `#f7f2e8`, ink
   `#1e2a33`, muted ink `#53616c`, line `#d7cdbb`, panel `#fffdf8`,
   terracotta accent `#aa432d`, teal focus `#074f57`). New class-marker
   tints must be derived by mixing ink/accent/paper (no new hue families),
   must pass 4.5:1 against their background when carrying text, and are
   never the sole carrier of meaning (C-4.4).
2. **Typography.** Display: a committed OFL variable serif loaded via
   `next/font/local` (self-hosted `woff2` in the repo, ≤ 2 files, ≤ 250KB
   total, `adjustFontFallback` on; no external font requests). Body: the
   existing Georgia/Times serif stack. Annotation: the existing
   Courier-family monospace for eyebrows, chips, and provenance metadata.
   The date monument (C-3.1) is the page's LCP element and must render on
   the fallback stack without layout shift beyond the font-swap metrics
   adjustment.
3. **Texture.** Flat paper only: rules, hatching, and spacing create
   depth. No drop-shadow stacks, no glassmorphism, no gradients except a
   single optional paper-tone wash, no imagery, no icons standing alone
   without text.
4. Dark theme: out of scope (C-15).

## C-3 Page anatomy: /day/[date]

Top to bottom:

1. **Arrival panel** (`data-testid="day-arrival"`): eyebrow ("Historical
   perspective"), the **date monument** (date written out, display face),
   an era line naming the band plainly — "Limited historical era ·
   1900–1949" / "Standard statistical era · 1950–1988" / "Enhanced
   structured era · 1989–2025" — and the publication status line. Band
   text derives from `profileTypeForDate` only.
2. **Strata**, in fixed order (`data-testid="stratum-<section_key>"`):
   `recorded_on_this_date`, `typical_day_in_this_year`,
   `wider_historical_context`, `curated_claims`, `derived_comparisons`,
   `wonder_and_progress`, `evidence_notes`. Order is stable across dates;
   emphasis is not (C-5).
3. **Publication integrity strip** (inside the evidence-notes stratum,
   `data-testid="publication-integrity"`): profile version context —
   manifest id, content hash (truncated with full value on
   expand/copy), and top-level quality grade + explanation, source
   attribution. Closes the audit §3.2 gap.
4. **Depth rail / date bar** (C-6): sticky right rail ≥ 64rem viewports;
   sticky bottom bar below that.

Within a stratum, statements use the document register: statement text in
body serif at full measure; class chip + caveat line (C-4); provenance
affordance (C-9). **Lead-statement emphasis:** the payload is a flat
statement array with no event or group identifier (the golden artifact
renders one earthquake as eight independent statements), so event
grouping is NOT derivable and the UI must never claim an event count.
Instead, when `recorded_on_this_date` is non-empty its first statement
(publisher order) takes the lead treatment — larger statement type and
full-width rule — and the remainder render in the standard register. A
payload-level grouping signal is deferred to backend coordination
(logged on issue #18).

## C-4 Evidence classes

1. **Classes and derivation.** Every rendered statement gets exactly one
   base class from `deriveEvidenceClass(sectionKey, statement)` — a pure,
   never-throwing function using, in precedence order: (a) typed signals —
   the section key and the **provenance root**, meaning the validated
   presence of a `resolved_claim` vs `derived_value` object (the payload
   validator requires at least one, `day-profile.ts:103`; the optional,
   unvalidated `root_type` field may corroborate but is never the
   discriminant); (b) runtime-checked optional `details` markers —
   `temporal_assignment`, `data_status` — as refinement only. Unknown or
   malformed markers degrade to the section-default class, never to a
   crash or a stronger claim. In the table below, "root X" refers to this
   validated-presence definition.

| Class key | Base derivation | Chip label (canonical) | Edge treatment |
| --- | --- | --- | --- |
| `recorded` | section `recorded_on_this_date` + root `resolved_claim` | "Recorded on this date" | solid ink rule |
| `daily-average` | root `derived_value` + `temporal_assignment: uniform_period_allocation` (default for `typical_day_in_this_year`) | "Annual daily average" | dotted rule |
| `date-modeled` | root `derived_value` + `temporal_assignment: modeled_period_allocation` (no data instances today — mock-tested only) | "Modeled for this date" | dashed rule |
| `period-context` | `temporal_assignment: period_context`/`editorial_context` (default for `wider_historical_context`) | "Period context" | hatched rule |
| `curated` | section `curated_claims` | "Curated claim" | bracketed rule |
| `comparison` | section `derived_comparisons` | "App-derived comparison" | double rule |
| `archive-note` | section `evidence_notes` | "About this evidence" | plain rule |
| `unavailable` | `data_status: missing` or missing-value derived statements | "Not available" | faded rule |
| `unclassified` | statement without a `provenance` block (valid per the payload validator) in any section | "Evidence class unstated" | faded dotted rule |

2. **Caveat lines.** `daily-average` statements always carry, visibly and
   adjacent: "Average across {year} — not a count for this date."
   `period-context` carries: "Describes the surrounding period, not this
   date specifically." `date-modeled` carries: "Modeled estimate for this
   date, not a recorded observation." `comparison` carries its
   comparability status when the payload provides one. {year} comes only
   from payload/date data.
3. **Disputed marker.** `provenance.dissenting_claims.length > 0` adds
   the badge "Disputed — sources disagree" alongside (never replacing)
   the base class, and the evidence panel presents the dissenting records
   with the same completeness as supporting ones. Zero real instances
   exist today; mock-tested.
4. **Never color alone.** Every class is distinguishable by chip text +
   edge treatment with color stripped (verified by test asserting label
   text presence per class).
5. `wonder_and_progress` statements classify by their own signals
   ((a)/(b) above), not by a section default, until real data exists.
6. **Conservative fallback.** A statement with no `provenance` block —
   valid, since the validator treats provenance as optional
   (`day-profile.ts:114`) — classifies as `unclassified` in every
   section, including `wonder_and_progress`. The fallback understates
   rather than overstates: it never inherits a stronger section default,
   and B1's unit tests cover it explicitly. This satisfies C-4.1's
   exactly-one-class rule for the entire valid payload space.

## C-5 Variable content, quiet dates

1. Emphasis adapts to content: a date with recorded statements leads
   with them (lead-statement emphasis, C-3); a date with none leads with
   the average-day stratum and says plainly that no reviewed event is
   published for this date.
2. Empty or unsupported strata compress to a **seam**: stratum heading,
   one-line state text, no card boxes. The three states keep distinct
   canonical text (C-8) and visual weight far below populated strata.
3. Nothing may pad a quiet date: no filler facts, no decorative quotes,
   no "did you know".

## C-6 Navigation

1. The date bar/rail (`data-testid="day-nav"`) is present on **every**
   `/day/*` render — published, unpublished, error, invalid, loading —
   and offers: previous day, next day, another date (opens the date
   form), random day. All ≥ 44px touch targets, all keyboard reachable,
   arrow affordances labeled with the actual target date ("← March 26,
   1964"). On a render with no valid reference date (the malformed-path
   state of C-8.2), previous/next are omitted — never labeled with an
   invented date — while another-date and random day (which needs no
   reference) remain available.
2. Previous/next clamp at `1900-01-01`/`2025-12-31` (disabled state at
   the edge, never wrapping). Random draws uniformly from the full shell.
3. Navigation is **blind by design** (no published-dates index exists):
   landing on an unpublished date is a normal, honest arrival, not an
   error.
4. URLs are canonical: `/day/YYYY-MM-DD`. A parseable but non-canonical
   date path (e.g. `/day/1964-3-27`) redirects (308) to the canonical
   form. Non-date paths render the invalid state (C-8).
5. Every `/day/[date]` page sets per-date metadata (title "March 27, 1964 —
   Day Perspective"; description naming the era band). The landing page
   keeps the product title.

## C-7 Motion

1. CSS-only. Choreography is keyed off `data-phase` on the profile shell:
   `traveling → arrived` (plus `instant` under reduced motion). No JS
   animation libraries, no `waitForTimeout`-shaped test hooks.
2. **No artificial delay, ever.** The traveling phase lasts exactly as
   long as the real fetch/validation. Content renders the moment it is
   ready; animation may accompany, never gate.
3. Budgets: initial arrival stagger ≤ 640ms total (per-stratum offsets
   60–90ms, transform/opacity only); adjacent-date change ≤ 220ms; micro
   interactions ≤ 150ms. Nothing loops except the loading pulse.
4. `prefers-reduced-motion: reduce` disables **all** non-essential
   animation and transition: instant state swaps, static loading bar with
   text instead of pulse. Equivalent information, zero motion.
5. Motion never moves text a reader may be mid-reading, never triggers on
   scroll, never parallaxes.

## C-8 Data states (canonical strings)

1. Frozen as-is (already test-pinned, kept deliberately):
   - Unpublished heading: **"This day does not have a published profile
     yet."** with body "An evidence-backed publication manifest has not
     been created for this date. No historical facts are substituted for
     the missing profile."
   - Loading eyebrow: **"Checking publication status"**.
   - API error heading: **"The profile could not be loaded."** + retry
     action "Retry profile request".
   - Provenance affordance: **"Why can the app say this?"**
   - Dissent-empty line: **"None in this publication."**
2. Changed (implemented in slice G, updating their pinned assertions in
   the same PR):
   - Out-of-range valid date: heading **"This date is outside the public
     range."** body "Records span 1900-01-01 through 2025-12-31."
   - Malformed path (not a canonical date, not redirectable): heading
     **"This address is not a calendar date."** body "Use the form
     YYYY-MM-DD, between 1900-01-01 and 2025-12-31."
3. Section-state text: `not_yet_supported` shows the payload `reason`
   verbatim when present, else "This section is not yet supported by an
   implemented pipeline."; published-but-empty shows "No evidence-backed
   content was published for this section."; both render as seams (C-5.2).
4. Missing values render their `missing_data_explanation`/reason when the
   payload provides one. Never zero-fill, never estimate in copy.

## C-9 Evidence interaction

1. Every statement's full chain — resolved claim / derived value,
   supporting claims with source-record links, dissenting claims, source
   release, methodology, quality — reachable within **≤ 2 interactions**
   from the statement (`data-testid="evidence-panel"`).
2. The panel is a native-disclosure-based drawer (desktop side panel,
   mobile bottom sheet), focus-contained while open, Esc-closable,
   restoring focus to its trigger on close.
3. Exposes only what the published artifact exposes: no internal
   filesystem paths, no secrets, no meaningless internal identifiers.
   Manifest id and content hash are public provenance and are shown
   (C-3.3).
4. Provenance completeness never regresses below the current rendering
   (audit §2 strengths list, item 4).

## C-10 Accessibility

1. Landmarks: header (arrival), nav (date bar), main, plus a skip link to
   main as first focusable element. One `h1` per page (the date
   monument); strata headings `h2`; logical order below.
2. Keyboard: all interactions reachable and operable; no traps; visible
   `:focus-visible` (existing 3px token) everywhere.
3. On nav-initiated date change, focus moves to the arrival heading and
   the new date is announced (`aria-live="polite"`). Initial page load
   never steals focus.
4. Loading and error states announced (`aria-busy`, `aria-live`, existing
   pattern kept).
5. Contrast ≥ 4.5:1 for text, ≥ 3:1 for structural marks; no information
   by color alone (C-4.4); touch targets ≥ 44px.
6. Motion per C-7.4.

## C-11 Selector contract

User-facing assertions use role + accessible name against canonical
strings (C-8). Structural locators use `data-testid` from this fixed
vocabulary: `day-arrival`, `day-nav`, `stratum-<section_key>`,
`statement`, `evidence-chip`, `evidence-panel`,
`publication-integrity`, `state-panel`. The fragile heading-parent walk
in `full-stack-golden.spec.ts:33-35` is replaced by
`stratum-evidence_notes` in slice B2. No spec asserts animation frames.

## C-12 Performance

1. No new runtime dependencies. Fonts per C-2.2 (committed, preloaded by
   `next/font`). No render-blocking decorative assets, no images required
   for first paint.
2. No layout shift on arrival: the arrival panel and strata seams reserve
   their space; CLS from the redesign ≈ 0 beyond font metric adjustment.
3. The date monument is the LCP; nothing may delay it behind data (it
   derives from the URL, renderable server-side immediately).

## C-13 CSS architecture

`app/globals.css` splits into `app/styles/tokens.css` (design tokens —
single source of truth), `base.css`, `landing.css`, `profile.css`,
`admin.css`, imported from `app/layout.tsx` (slice A0, pure move). All
**new** arc components use CSS Modules co-located with the component;
shared visual vocabulary (chips, rules, seams) lives in `profile.css`.
No CSS-in-JS, no Tailwind, no new tooling.

## C-14 Slice acceptance criteria

- **A0**: styles split + Playwright projects `mobile-chromium` (Pixel 7)
  and `reduced-motion` (`contextOptions.reducedMotion`), pulse guarded;
  existing mocked e2e passes unchanged on all three projects; zero visual
  diff intent (only the reduced-motion guard changes behavior).
- **B1**: `deriveEvidenceClass` pure lib with exhaustive unit tests
  (every class, fallback, malformed-details never-throws); chips +
  caveats per C-4 on the golden profile; `quality` + `source_attribution`
  rendered; disputed badge mock-tested; no `packages/contracts` change.
- **B2**: page anatomy per C-3 (arrival panel, strata order, seams per
  C-5.2, lead-statement rule); `unpublished-state.spec.ts` rewritten
  first; parent-walk locator replaced (C-11); mobile project green.
- **C**: date bar per C-6 on all `/day/*` states; date math unit-tested
  across leap/month/year/clamp boundaries before the component exists;
  canonical redirect + per-date metadata; new `e2e/navigation.spec.ts`.
- **B3**: evidence panel per C-9; publication integrity strip per C-3.3;
  the six audit-enumerated pinned provenance assertions updated in the
  same PR; full-stack spec passes against the real artifact.
- **E**: `data-phase` choreography per C-7; reduced-motion project
  asserts instant, motionless equivalence; interruption (date change
  mid-travel) covered by the existing abort/reset tests plus one new
  case.
- **F**: landing per Strata (horizon band, era labels, entry on the
  line); `DateInputForm` props contract unchanged; new `e2e/landing.spec.ts`
  (valid entry navigates; invalid shows `role="alert"`; keyboard-only
  path) — first-ever landing coverage.
- **G**: state panels + route error/loading files in final design; C-8.2
  string changes with their assertions; skip link + focus management per
  C-10; a11y checklist pass recorded in the PR; arc-close docs.

## C-15 Scope and explicit deferrals

Deferred (tracked as issues, not designed here): dark theme;
published-dates index/heatmap (needs backend endpoint); map or timeline
visualizations; search; bookmarks/favorites; typed `details` vocabulary
in `packages/contracts` (backend-coordination issue); CI docs-only fast
path; admin console redesign; Golden-100-dependent editorial surfaces
(curated/wonder/comparison strata render their honest seams until
pipelines exist).
