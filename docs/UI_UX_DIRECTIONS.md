# UI/UX Directions — Three Concepts and a Choice

Companion to `docs/UI_UX_AUDIT.md` (ground truth) and input to
`docs/UI_UX_CONTRACT.md` (the binding design contract). Three genuinely
different concepts were developed against the same constraints: the real
`PublishedDayProfile` contract, one published date, sparse-by-design
sections, zero fabricated data, no new runtime dependencies, CSS-only
motion, and honest empty states as first-class material.

---

## Direction A — "The Ledger Leaf" (a living archival document)

**Core metaphor.** Every date is one leaf of an unbound, still-being-written
world ledger. The app does not *show* a dashboard about a day; it *opens the
record* for that day.

- **Landing.** A nearly empty sheet: masthead, an edition line ("Records
  span 1900–2025 · one leaf per day"), and a single oversized date entry.
- **Date entry.** The native date field styled as a ruled ledger cell;
  submitting "pulls the leaf."
- **Transition.** A page-turn implied by a single horizontal wipe
  (transform/opacity, ~300ms); reduced motion: instant replace.
- **Arrival.** A document header: the date spelled out as typographic
  monument, era band as an edition note beneath it.
- **Information structure.** One continuous single-column document.
  Sections are running heads; statements are numbered entries; evidence
  classes are marginal sigils + small-caps labels in a left margin rail;
  provenance is footnotes.
- **Navigation.** Page corners: "← previous leaf / next leaf →" at top and
  bottom; a slim bottom bar on mobile.
- **Mobile.** The margin rail collapses to inline labels above each entry;
  the document column is native to phones.
- **Evidence interaction.** Footnote references open an evidence panel
  (drawer on desktop, bottom sheet on mobile).
- **Strengths.** Cheapest to build; strongest "record, not content" voice;
  typography does all the work; sparse dates read as short leaves, which is
  honest rather than empty-looking.
- **Risks.** Sits closest to the banned fake-newspaper cliché — survives
  only with rigorously modern typography (no aging, no columns-for-show).
  Simultaneity reads as a list; the "travel" feeling is weakest of the
  three. Margin-rail class labels weaken on mobile, where the class
  distinction matters most.
- **Technical cost.** Low. **Data compatibility.** Total.
- **Deliberately avoids.** Any pictorial ornament, maps, charts.

## Direction B — "Strata" (descent through simultaneous layers)

**Core metaphor.** A date is a place you descend to. A day's reality exists
in strata: what was *recorded* that day (bedrock), what *flowed* through
every day of that year (currents), the *climate* of the surrounding period,
and the archive's own voice about what it knows. Simultaneity is vertical
depth, not a list.

- **Landing.** A horizon: a thin ruled band spanning 1900–2025 drawn across
  the page like a geological cross-section, the three era bands labeled on
  it; the date entry sits on the horizon line. The supported range is
  *drawn*, not footnoted.
- **Date entry.** Native date field + "Descend" action; era band under the
  cursor's year is named live from the typed date (from
  `profileTypeForDate`, no fabrication).
- **Transition.** Departure: the horizon band holds; strata slide up into
  place beneath it with a 60–90ms stagger (transform/opacity only,
  ≤ 640ms total). The travel phase lasts exactly as long as the real fetch
  — no artificial dwell. Reduced motion: instant, complete page.
- **Arrival.** A "WHEN YOU ARE" panel: the date as typographic monument;
  under it, plainly, the era band ("Standard statistical era · 1950–1988")
  and the publication status. Nothing else outranks it.
- **Information structure.** Strata bands, each with its own visual
  grammar — *not* equal cards: Recorded (sharp rules, dated, dominant
  event treatment when one event dominates), The average day (flowing,
  explicitly chip-labeled annual equivalents), Period climate (wide,
  hatched edges, year-labeled), Curated & comparisons (bracketed, visibly
  editorial), Evidence notes (the archive speaking in its own voice,
  including quality grade and manifest hash). Empty strata compress to
  thin, labeled seams — an honest map's blank regions.
- **Navigation.** A persistent depth rail: desktop right edge (sticky) —
  previous day, next day, another date, random day, plus stratum
  positions; mobile: a sticky bottom bar with the same four actions.
- **Mobile.** Strata stack naturally; the class grammar (edge marks +
  chips) survives at one column; the bottom bar keeps navigation reachable
  from any depth.
- **Evidence interaction.** Any statement opens the evidence panel (side
  drawer / bottom sheet): claim chain, supporting and dissenting records,
  source release, methodology, quality — ≤ 2 interactions from statement
  to full chain.
- **Strengths.** The core mechanic *is* the product's non-negotiable:
  evidence classes are visibly different strata treatments, structural on
  every viewport. Travel is expressed by architecture (descent, depth
  rail), not decoration, so it stays fast on repeat use. Extends cleanly
  as datasets arrive (new strata, same grammar).
- **Risks.** Largest CSS surface of the three; the layer grammar must be
  disciplined or it decays into "cards with colored borders." A stratum
  with one statement must still look intentional. More visual decisions =
  more reviewer-taste surface (contained by the contract).
- **Technical cost.** Medium — layout, typography, tokens; no canvas, no
  libraries. **Data compatibility.** Total; designed from the real
  16-statement artifact and its `section_states`.
- **Deliberately avoids.** Maps, timelines-of-the-day, any visualization
  implying more data than exists; equal-card grids; dark theatrics.

## Direction C — "The Signal Room" (a listening station for one day)

**Core metaphor.** Instruments tuned to one date. Recorded events are
*signals*; statistical averages are the *background hum*; period context is
*weather*; curated material is the *operator's log*.

- **Landing.** A dark instrument surface; a large date dial; tuning as the
  entry gesture.
- **Transition.** "Tuning" — a brief signal-lock animation.
- **Arrival.** A frequency-band layout: channels for signal / hum / weather
  / log, each visually instrument-like (mono type, rules, meters without
  fake needles).
- **Navigation.** Dial steps (±1 day), a scan (random), a retune (change).
- **Evidence.** "Trace" panels per signal.
- **Strengths.** Most visually distinctive; the signal/background split is
  a genuinely good epistemic metaphor for recorded-vs-average.
- **Risks.** Fails "calm enough to read" for long-form reading; the
  station fiction implies live observation of the past — a tone lie the
  product cannot afford; dark inversion churns every existing style and
  test for aesthetics, not honesty; meters and dials skirt the banned
  decorative-machinery cliché; weakest fit for curated claims and
  apocalypse records (not "signals").
- **Technical cost.** High. **Data compatibility.** Strained — channels
  imply continuous data where the artifact has 16 statements.
- **Deliberately avoids.** Paper metaphors entirely.

---

## Decision

**Chosen: Direction B — Strata, carrying Direction A's typographic
register inside each stratum.** The document voice of A (numbered entries,
running heads, footnote-grade provenance) becomes the *within-stratum*
typography; B supplies the macro-structure, arrival, motion, and
navigation.

Why B over A: the evidence-class distinction is the product's
non-negotiable, and A hangs it on a margin rail that collapses precisely
on mobile where misreading risk is highest; B makes the distinction the
architecture. A's travel feeling is also weakest — repeat users would
experience a list with a nice header.

Why B over C: C's tone fights the product (theatrical, dark, implying
live observation); its cost lands in aesthetics rather than honesty; its
channel metaphor fabricates continuity the data does not have. C's one
great idea — signal vs background — survives in B as the
recorded-vs-average stratum grammar.

Consequences recorded in `docs/UI_UX_CONTRACT.md` (binding clauses) and
`docs/DECISIONS.md` (D-entry, in the first implementation slice).
