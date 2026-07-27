import { describe, expect, it } from "vitest";

import type { CoverageDateResponse } from "@day-perspective/contracts";

import { discoveryStateFor } from "./coverage";
import {
  describeContextHeld,
  describeEmptyIndex,
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
