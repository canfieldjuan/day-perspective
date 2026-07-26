import type { DiscoveryState, EnrichedDestination } from "./coverage";
import { formatPublicDate } from "./date";

/**
 * Every sentence the coverage interface says, derived from the facts that
 * license it.
 *
 * Six review rounds on this slice each found copy asserting something the
 * index does not carry, and a vocabulary module that merely *named* the
 * terms did not stop it — the defects were in which branch won and which
 * branch was missed, not in the words themselves. So the sentences are
 * built here from the state, in one place, where the combinations are
 * visible together and a new one cannot be silently unhandled.
 *
 * The terms and their evidence:
 *
 * - **enriched** — `publication_tier != context_only`, which is what the
 *   `nearest_enriched_*` fields mean. Not "evidence-backed": every
 *   published date is that, including all 27,758 context-only ones, so
 *   denying such dates exist would be false. Not "reviewed": coverage
 *   carries no review-status field (removed in AA3a; see issue #45).
 * - **recorded event** — `has_recorded_event`, and per destination by
 *   comparison with `nearest_recorded_event_*`. A `partially_enriched`
 *   date is enriched without holding one.
 * - **demographic context** — a non-zero `typical_day_in_this_year`. Not
 *   `wider_historical_context`, which is any surrounding-period condition
 *   and is where UCDP publishes armed-conflict counts.
 * - **period context** — a non-zero `wider_historical_context`.
 */

/** What this page holds, named exactly. Null when it holds neither kind. */
export function describeContextHeld(state: DiscoveryState): string | null {
  const { hasDemographicContext, hasPeriodContext } = state;
  if (hasDemographicContext && hasPeriodContext) {
    return "demographic and period context";
  }
  if (hasDemographicContext) {
    return "demographic context";
  }
  if (hasPeriodContext) {
    return "period context";
  }
  return null;
}

/** The arrival sentence for a context-only page. */
export function describeSparsePage(
  state: DiscoveryState,
  monument: string
): string {
  const held = describeContextHeld(state);
  const absence = `No recorded events are published for ${monument}.`;
  return held === null
    ? absence
    : `This date currently has ${held} only. ${absence}`;
}

/** The lead line above a single destination, graded by real distance. */
export function describeDestinationLead(
  destination: EnrichedDestination
): string {
  if (destination.band === "days") {
    return "Continue to a richer date";
  }
  if (destination.band === "months") {
    return destination.sameCalendarYear
      ? `An enriched date is available ${destination.direction} in the year`
      : "The closest enriched date is a few months away";
  }
  return "The closest enriched date is farther away";
}

/** What a single destination offers, and how far away it is. */
export function describeDestination(destination: EnrichedDestination): string {
  const target = formatPublicDate(destination.date) ?? destination.date;
  if (destination.band !== "days") {
    return `${target}, ${destination.distance}.`;
  }
  // Only a destination that holds a recorded event may be said to.
  const holds = destination.hasRecordedEvent
    ? "has recorded events"
    : "carries more than annual context";
  return `${target} ${holds}, ${destination.distance}.`;
}

/** The label for one destination in a two-direction list. */
export function describeDestinationLabel(
  destination: EnrichedDestination
): string {
  const target = formatPublicDate(destination.date) ?? destination.date;
  return destination.distance
    ? `${target} · ${destination.distance}`
    : target;
}

/** Explains the direction that holds nothing, rather than leaving a gap. */
export function describeMissingDirection(
  state: DiscoveryState,
  monument: string
): string | null {
  if (state.missingDirection === null) {
    return null;
  }
  const side = state.missingDirection === "after" ? "after" : "before";
  return `No enriched date is currently published ${side} ${monument}.`;
}

/** What to say when the archive offers no enriched destination at all. */
export function describeEmptyIndex(state: DiscoveryState): string {
  const held = describeContextHeld(state);
  const alternatives =
    held === null
      ? "You can continue chronologically or choose another date."
      : `You can continue chronologically, choose another date, or explore the available ${held}.`;
  return `No enriched dates are currently available from this archive index. ${alternatives}`;
}


/**
 * The landing page's disclosure, built only from aggregates the summary
 * actually carries: the published total, the range, the tier breakdown and
 * the recorded-event count.
 *
 * It deliberately does not say what the remaining dates contain. A
 * `partially_enriched` profile can hold curated or comparison content and
 * no period context at all, and the summary exposes no aggregate that
 * would prove otherwise — so claiming it would be inventing a fact from a
 * count.
 */
export function describeArchiveShape(summary: {
  total_published: number;
  by_tier: Record<string, number>;
  with_recorded_event: number;
  earliest: string | null;
  latest: string | null;
}): { scale: string; caveat: string } {
  const span =
    summary.earliest && summary.latest
      ? `, from ${formatPublicDate(summary.earliest) ?? summary.earliest} to ${
          formatPublicDate(summary.latest) ?? summary.latest
        }`
      : "";
  const scale = `${summary.total_published.toLocaleString(
    "en-US"
  )} dates are published${span}.`;

  const contextOnly = summary.by_tier.context_only ?? 0;
  const events = summary.with_recorded_event;
  const eventSentence =
    events === 0
      ? "No date yet carries a recorded event."
      : events === 1
        ? "One carries a recorded event."
        : `${events.toLocaleString("en-US")} carry recorded events.`;
  const contextSentence =
    contextOnly === 0
      ? ""
      : ` ${contextOnly.toLocaleString("en-US")} carry the annual context of their year and no recorded event.`;

  return { scale, caveat: `${eventSentence}${contextSentence}` };
}

/** Explains a direction with no enriched date, for the discovery nav. */
export function describeNavAbsence(
  missingDirection: "before" | "after" | null,
  monument: string
): string {
  if (missingDirection === null) {
    return "";
  }
  const side = missingDirection === "after" ? "after" : "before";
  return `No enriched date is currently published ${side} ${monument}.`;
}
