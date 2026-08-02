import { describe, expect, it } from "vitest";

import {
  PUBLICATION_TIERS,
  REVIEW_STATUSES,
  type CoverageDateResponse,
  type PublicationTier,
  type ReviewStatus
} from "@day-perspective/contracts";

import { discoveryStateFor, isCoverageResponse } from "./coverage";
import {
  describeContextHeld,
  describeDestination,
  describeDestinationLabel,
  describeDestinationLead,
  describeEmptyIndex,
  describeMissingDirection,
  describeSparsePage
} from "./coverage-copy";

const base: CoverageDateResponse = {
  status: "coverage",
  date: "1983-10-12",
  profile_type: "standard_statistical",
  publication_tier: "context_only",
  review_status: "automated_only",
  quality_floor: "not_assessed",
  has_recorded_event: false,
  sections: {},
  nearest_enriched_before: null,
  nearest_enriched_after: null,
  nearest_recorded_event_before: null,
  nearest_recorded_event_after: null
};

function stateFor(sections: Record<string, number>) {
  return discoveryStateFor({ ...base, sections }, "1983-10-12");
}

/**
 * Every combination of the facts that license a sentence, together, so a
 * new one cannot be silently unhandled or shadowed by an earlier branch —
 * which is how the previous rounds' defects reached the page.
 */
describe("the sentence follows the facts", () => {
  const combinations: Array<[Record<string, number>, string | null]> = [
    [{ typical_day_in_this_year: 2, wider_historical_context: 3 }, "demographic and period context"],
    [{ typical_day_in_this_year: 2 }, "demographic context"],
    [{ wider_historical_context: 3 }, "period context"],
    [{ evidence_notes: 1 }, null],
    [{}, null]
  ];

  it("names exactly what each combination holds", () => {
    for (const [sections, expected] of combinations) {
      expect(describeContextHeld(stateFor(sections))).toBe(expected);
    }
  });

  it("states the absence of recorded events in every combination", () => {
    for (const [sections] of combinations) {
      const sentence = describeSparsePage(stateFor(sections), "October 12, 1983");
      expect(sentence).toContain(
        "No recorded events are published for October 12, 1983."
      );
      expect(sentence).not.toContain("reviewed");
      expect(sentence).not.toContain("evidence-backed");
    }
  });

  it("offers only context the page can actually show", () => {
    expect(describeEmptyIndex(stateFor({ typical_day_in_this_year: 2 }))).toContain(
      "explore the available demographic context"
    );
    expect(describeEmptyIndex(stateFor({ wider_historical_context: 3 }))).toContain(
      "explore the available period context"
    );
    const nothing = describeEmptyIndex(stateFor({ evidence_notes: 1 }));
    expect(nothing).toContain("continue chronologically or choose another date");
    expect(nothing).not.toContain("explore");
  });
});

// --- Vocabulary invariance across tier x review-state (epic #64, MD4) -------
//
// MD1 moved review status off the richness axis and left it there: the
// coverage response still carries review_status, but the enrichment
// vocabulary must never read it. This locks that — for a fixed tier every
// sentence is identical across all three review states — and checks the
// vocabulary is classified by tier, closing the last item of #64's
// verification checklist.

const DATE = "1983-10-12";
const MONUMENT = "October 12, 1983";

function coverageFor(
  tier: PublicationTier,
  review: ReviewStatus
): CoverageDateResponse {
  return {
    status: "coverage",
    date: DATE,
    profile_type: "standard_statistical",
    publication_tier: tier,
    review_status: review,
    quality_floor: "not_assessed",
    has_recorded_event: tier === "enriched",
    sections:
      tier === "enriched"
        ? { recorded_on_this_date: 1 }
        : { typical_day_in_this_year: 2, wider_historical_context: 3 },
    // A destination each way, so the discovery copy has something to say
    // whatever the tier.
    nearest_enriched_before: "1980-06-15",
    nearest_enriched_after: "1990-06-15",
    nearest_recorded_event_before: "1980-06-15",
    nearest_recorded_event_after: "1990-06-15"
  };
}

/** Every sentence the coverage vocabulary can build from one page's state. */
function everySentence(coverage: CoverageDateResponse): string[] {
  const state = discoveryStateFor(coverage, DATE);
  const lines = [
    `held:${describeContextHeld(state)}`,
    `sparse:${describeSparsePage(state, MONUMENT)}`,
    `empty:${describeEmptyIndex(state)}`,
    `missing:${describeMissingDirection(state, MONUMENT)}`
  ];
  for (const [side, destination] of [
    ["before", state.before],
    ["after", state.after]
  ] as const) {
    if (destination !== null) {
      lines.push(`${side}-lead:${describeDestinationLead(destination)}`);
      lines.push(`${side}-body:${describeDestination(destination)}`);
      lines.push(`${side}-label:${describeDestinationLabel(destination)}`);
    }
  }
  return lines;
}

describe("the enrichment vocabulary ignores review status", () => {
  it("feeds only contract-valid coverage for every tier x review pair", () => {
    for (const tier of PUBLICATION_TIERS) {
      for (const review of REVIEW_STATUSES) {
        expect(isCoverageResponse(coverageFor(tier, review), DATE)).toBe(true);
      }
    }
  });

  it("says the same thing whatever the review status, for every tier", () => {
    for (const tier of PUBLICATION_TIERS) {
      const [first, ...rest] = REVIEW_STATUSES.map((review) =>
        everySentence(coverageFor(tier, review))
      );
      for (const sentences of rest) {
        expect(sentences).toEqual(first);
      }
    }
  });

  it("never says 'reviewed' or 'evidence-backed' in any pair", () => {
    for (const tier of PUBLICATION_TIERS) {
      for (const review of REVIEW_STATUSES) {
        for (const sentence of everySentence(coverageFor(tier, review))) {
          expect(sentence).not.toContain("reviewed");
          expect(sentence).not.toContain("evidence-backed");
        }
      }
    }
  });

  it("classifies discovery by tier, not review status", () => {
    for (const review of REVIEW_STATUSES) {
      expect(
        discoveryStateFor(coverageFor("context_only", review), DATE).kind
      ).toBe("both-directions");
      expect(
        discoveryStateFor(coverageFor("partially_enriched", review), DATE).kind
      ).toBe("on-enriched-date");
      expect(
        discoveryStateFor(coverageFor("enriched", review), DATE).kind
      ).toBe("on-enriched-date");
    }
  });
});
