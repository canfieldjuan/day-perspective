import {
  DAY_PROFILE_SECTION_KEYS,
  PUBLICATION_TIERS,
  type CoverageDateResponse,
  type PublicationTier,
  type RandomEnrichedResponse
} from "@day-perspective/contracts";

import { describeDistance, distanceBand, type DistanceBand } from "./coverage-distance";
import { isSupportedPublicDate } from "./date";

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  return value as Record<string, unknown>;
}

function isNeighbour(value: unknown): boolean {
  return value === null || (typeof value === "string" && isSupportedPublicDate(value));
}

/**
 * Section counts must be real counts under known keys.
 *
 * Accepting any object let `true` or `"2"` coerce through the numeric
 * comparisons downstream and render a confident claim about content the
 * page may not have — which is the one thing this validator exists to
 * prevent.
 */
function isSectionCounts(value: unknown): boolean {
  const record = asRecord(value);
  if (record === undefined) {
    return false;
  }
  return Object.entries(record).every(
    ([key, count]) =>
      (DAY_PROFILE_SECTION_KEYS as readonly string[]).includes(key) &&
      typeof count === "number" &&
      Number.isInteger(count) &&
      count >= 0
  );
}

/**
 * Runtime check before any coverage value reaches the interface. The API is
 * trusted to be correct, not to be reachable: a proxy error or a version
 * skew must not render as a confident claim about the archive.
 */
export function isCoverageResponse(
  payload: unknown,
  expectedDate: string
): payload is CoverageDateResponse {
  const response = asRecord(payload);
  return (
    response !== undefined &&
    response.status === "coverage" &&
    response.date === expectedDate &&
    typeof response.profile_type === "string" &&
    (PUBLICATION_TIERS as readonly string[]).includes(
      response.publication_tier as string
    ) &&
    typeof response.has_recorded_event === "boolean" &&
    isSectionCounts(response.sections) &&
    isNeighbour(response.nearest_enriched_before) &&
    isNeighbour(response.nearest_enriched_after) &&
    isNeighbour(response.nearest_recorded_event_before) &&
    isNeighbour(response.nearest_recorded_event_after)
  );
}

export interface EnrichedDestination {
  date: string;
  distance: string | null;
  band: DistanceBand | null;
  direction: "earlier" | "later";
  /** True when the destination falls in the same calendar year as the page. */
  sameCalendarYear: boolean;
  /**
   * Whether this destination actually holds a reviewed recorded event.
   * "Enriched" spans partially_enriched too, which carries curated or
   * comparison content and no recorded event — so the interface must not
   * promise events it cannot show.
   */
  hasRecordedEvent: boolean;
}

export type DiscoveryKind =
  /** Coverage could not be read. Not the same as an empty archive. */
  | "unknown"
  /** This page is itself enriched; discovery is not the point of it. */
  | "on-enriched-date"
  /** Enriched dates lie in both directions. */
  | "both-directions"
  /** Exactly one direction has an enriched date; the other must be explained. */
  | "one-direction"
  /** No enriched destination is reachable from here. */
  | "none-available";

export interface DiscoveryState {
  kind: DiscoveryKind;
  /**
   * Whether the page carries the annual demographic equivalents, which live
   * in typical_day_in_this_year. Derived from that section alone:
   * wider_historical_context is any surrounding-period condition, and UCDP
   * publishes armed-conflict counts there, so its presence proves period
   * context rather than demographic context.
   */
  hasDemographicContext: boolean;
  /** Whether the page carries surrounding-period context of any kind. */
  hasPeriodContext: boolean;
  tier: PublicationTier | null;
  before: EnrichedDestination | null;
  after: EnrichedDestination | null;
  /** Which destination is closer, for focus order. Never preselects. */
  closer: "before" | "after" | null;
  /** Named so the interface can explain an absence instead of disabling a
   * control with no reason given. */
  missingDirection: "before" | "after" | null;
  /**
   * Gates the Random enriched control. Removed from the previous slice as
   * orphaned; it returns here with the control that reads it, because a
   * button leading nowhere is not an affordance.
   */
  hasAnyEnrichedDestination: boolean;
}

function destination(
  from: string,
  to: unknown,
  recordedEventDate: unknown
): EnrichedDestination | null {
  if (typeof to !== "string" || !isSupportedPublicDate(to)) {
    return null;
  }
  return {
    date: to,
    distance: describeDistance(from, to),
    band: distanceBand(from, to),
    direction: to > from ? "later" : "earlier",
    sameCalendarYear: to.slice(0, 4) === from.slice(0, 4),
    hasRecordedEvent: to === recordedEventDate
  };
}

function daysBetween(from: string, to: string): number {
  return Math.abs(
    new Date(`${to}T00:00:00Z`).getTime() - new Date(`${from}T00:00:00Z`).getTime()
  );
}

/**
 * Classify what discovery this page can honestly offer.
 *
 * The archive currently holds one enriched date in 27,759, so the common
 * outcomes are "one direction, decades away" and — on the enriched date
 * itself — "nowhere else to go". Both are distinct from "the archive holds
 * nothing", and the interface must not collapse them together.
 */
export function discoveryStateFor(
  coverage: CoverageDateResponse | null,
  date: string
): DiscoveryState {
  if (coverage === null) {
    return {
      kind: "unknown",
      hasDemographicContext: false,
      hasPeriodContext: false,
      hasAnyEnrichedDestination: false,
      tier: null,
      before: null,
      after: null,
      closer: null,
      missingDirection: null,
      };
  }

  const sections = coverage.sections ?? {};
  const hasDemographicContext = (sections.typical_day_in_this_year ?? 0) > 0;
  const hasPeriodContext = (sections.wider_historical_context ?? 0) > 0;

  const hasAnyEnrichedDestination =
    coverage.nearest_enriched_before !== null ||
    coverage.nearest_enriched_after !== null;

  const before = destination(
    date,
    coverage.nearest_enriched_before,
    coverage.nearest_recorded_event_before
  );
  const after = destination(
    date,
    coverage.nearest_enriched_after,
    coverage.nearest_recorded_event_after
  );
  const closer =
    before !== null && after !== null
      ? daysBetween(date, before.date) <= daysBetween(date, after.date)
        ? "before"
        : "after"
      : null;

  const tier = coverage.publication_tier;
  if (tier !== "context_only") {
    return {
      kind: "on-enriched-date",
      hasDemographicContext,
    hasPeriodContext,
      tier,
      before,
      after,
      closer,
      missingDirection: null,
      hasAnyEnrichedDestination: false
    };
  }

  if (before !== null && after !== null) {
    return {
      kind: "both-directions",
      hasDemographicContext,
    hasPeriodContext,
      tier,
      before,
      after,
      closer,
      missingDirection: null,
      hasAnyEnrichedDestination
    };
  }

  if (before !== null || after !== null) {
    return {
      kind: "one-direction",
      hasDemographicContext,
    hasPeriodContext,
      tier,
      before,
      after,
      closer,
      missingDirection: before === null ? "before" : "after",
      hasAnyEnrichedDestination
    };
  }

  return {
    kind: "none-available",
    hasDemographicContext,
    hasPeriodContext,
    tier,
    before: null,
    after: null,
    closer: null,
    missingDirection: null,
    hasAnyEnrichedDestination: false
  };
}


/** Validated against the shared contract rather than a local status check. */
function isRandomEnriched(payload: unknown): payload is RandomEnrichedResponse {
  const record = asRecord(payload);
  return (
    record !== undefined &&
    record.status === "enriched_date" &&
    typeof record.date === "string" &&
    isSupportedPublicDate(record.date)
  );
}

/**
 * Resolve a random enriched date through the same-origin proxy, or null
 * when the archive holds none. Null means hide the control: offering a
 * journey to nowhere is worse than offering nothing.
 */
export async function randomEnrichedDate(): Promise<string | null> {
  try {
    const response = await fetch("/api/coverage/enriched/random", {
      cache: "no-store",
      headers: { Accept: "application/json" }
    });
    if (!response.ok) {
      return null;
    }
    const payload: unknown = await response.json();
    return isRandomEnriched(payload) ? payload.date : null;
  } catch {
    return null;
  }
}
