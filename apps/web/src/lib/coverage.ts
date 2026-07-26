import {
  PUBLICATION_TIERS,
  type CoverageDateResponse,
  type PublicationTier
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
    asRecord(response.sections) !== undefined &&
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
   * Whether the page actually carries annual or period context. The
   * context_only tier also admits an evidence-notes-only profile, so the
   * demographic claim is derived from the sections rather than the tier.
   */
  hasDemographicContext: boolean;
  tier: PublicationTier | null;
  before: EnrichedDestination | null;
  after: EnrichedDestination | null;
  /** Which destination is closer, for focus order. Never preselects. */
  closer: "before" | "after" | null;
  /** Named so the interface can explain an absence instead of disabling a
   * control with no reason given. */
  missingDirection: "before" | "after" | null;
  /** Gates the Random enriched control: a button leading nowhere is not an
   * affordance. */
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
      tier: null,
      before: null,
      after: null,
      closer: null,
      missingDirection: null,
      hasAnyEnrichedDestination: false
    };
  }

  const sections = coverage.sections ?? {};
  const hasDemographicContext =
    (sections.typical_day_in_this_year ?? 0) > 0 ||
    (sections.wider_historical_context ?? 0) > 0;

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
  const hasAnyEnrichedDestination = before !== null || after !== null;
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
      tier,
      before,
      after,
      closer,
      missingDirection: null,
      hasAnyEnrichedDestination
    };
  }

  if (before !== null && after !== null) {
    return {
      kind: "both-directions",
      hasDemographicContext,
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
    tier,
    before: null,
    after: null,
    closer: null,
    missingDirection: null,
    hasAnyEnrichedDestination: false
  };
}
