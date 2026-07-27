import { describe, expect, it } from "vitest";

import type { CoverageDateResponse } from "@day-perspective/contracts";

import { discoveryStateFor, isCoverageResponse } from "./coverage";

const base = {
  status: "coverage",
  date: "1983-10-12",
  profile_type: "standard_statistical",
  publication_tier: "context_only",
  review_status: "automated_only",
  quality_floor: "not_assessed",
  has_recorded_event: false,
  sections: { typical_day_in_this_year: 2 },
  nearest_enriched_before: null,
  nearest_enriched_after: null,
  nearest_recorded_event_before: null,
  nearest_recorded_event_after: null
} satisfies CoverageDateResponse;

describe("isCoverageResponse", () => {
  it("accepts a well-formed record", () => {
    expect(isCoverageResponse(base, "1983-10-12")).toBe(true);
  });

  it("rejects a record for a different date", () => {
    expect(isCoverageResponse(base, "1983-10-13")).toBe(false);
  });

  it("rejects an unknown tier rather than rendering it", () => {
    expect(
      isCoverageResponse({ ...base, publication_tier: "very_rich" }, "1983-10-12")
    ).toBe(false);
  });

  it("rejects a neighbour that is not a date or null", () => {
    expect(
      isCoverageResponse({ ...base, nearest_enriched_after: 7 }, "1983-10-12")
    ).toBe(false);
  });

  it("rejects the not-indexed envelope, which is not a record", () => {
    expect(
      isCoverageResponse(
        { status: "coverage_not_indexed", date: "1983-10-12", detail: "x" },
        "1983-10-12"
      )
    ).toBe(false);
  });
});

/**
 * The five coverage states the discovery section must distinguish. Each
 * exists because the archive currently holds one enriched date in 27,759,
 * so most pages are sparse and the honest answer is usually "decades away"
 * or "nothing in that direction".
 */
describe("discoveryStateFor", () => {
  it("reports an enriched page as needing no discovery prompt", () => {
    const state = discoveryStateFor(
      { ...base, publication_tier: "enriched", has_recorded_event: true },
      "1964-03-27"
    );
    expect(state.kind).toBe("on-enriched-date");
    // Standing on the only enriched date is not an empty index, and must
  });

  it("reports both directions without preferring either", () => {
    const state = discoveryStateFor(
      {
        ...base,
        nearest_enriched_before: "1975-09-03",
        nearest_enriched_after: "1984-03-27"
      },
      "1983-10-12"
    );
    expect(state.kind).toBe("both-directions");
    expect(state.before?.date).toBe("1975-09-03");
    expect(state.after?.date).toBe("1984-03-27");
    // Focus goes to the closer destination; neither is preselected.
    expect(state.closer).toBe("after");
  });

  it("reports a single direction and names the missing one", () => {
    const state = discoveryStateFor(
      { ...base, nearest_enriched_before: "1964-03-27" },
      "1983-10-12"
    );
    expect(state.kind).toBe("one-direction");
    expect(state.before?.date).toBe("1964-03-27");
    expect(state.before?.distance).toBe("nineteen years earlier");
    expect(state.after).toBeNull();
    expect(state.missingDirection).toBe("after");
  });

  it("reports an empty enriched index distinctly from a sparse page", () => {
    const state = discoveryStateFor(base, "1983-10-12");
    expect(state.kind).toBe("none-available");
  });

  it("bands the single destination so copy can adapt", () => {
    const near = discoveryStateFor(
      { ...base, date: "1964-03-26", nearest_enriched_after: "1964-03-27" },
      "1964-03-26"
    );
    expect(near.after?.band).toBe("days");
    expect(near.after?.distance).toBe("one day later");

    const mid = discoveryStateFor(
      { ...base, date: "1964-08-15", nearest_enriched_before: "1964-03-27" },
      "1964-08-15"
    );
    expect(mid.before?.band).toBe("months");
    expect(mid.before?.distance).toBe("about five months earlier");

    const far = discoveryStateFor(
      { ...base, nearest_enriched_before: "1964-03-27" },
      "1983-10-12"
    );
    expect(far.before?.band).toBe("years");
  });

  it("treats an unavailable coverage answer as unknown, not as empty", () => {
    const state = discoveryStateFor(null, "1983-10-12");
    expect(state.kind).toBe("unknown");
  });
});

describe("evidential honesty about the destination", () => {
  it("marks a destination that genuinely holds a recorded event", () => {
    const state = discoveryStateFor(
      {
        ...base,
        nearest_enriched_after: "1983-10-17",
        nearest_recorded_event_after: "1983-10-17"
      },
      "1983-10-12"
    );
    expect(state.after?.hasRecordedEvent).toBe(true);
  });

  it("does not promise recorded events for a partially enriched neighbour", () => {
    // nearest_enriched_* spans partially_enriched, which carries curated or
    // comparison content and no recorded event.
    const state = discoveryStateFor(
      {
        ...base,
        nearest_enriched_after: "1983-10-17",
        nearest_recorded_event_after: "1990-01-01"
      },
      "1983-10-12"
    );
    expect(state.after?.hasRecordedEvent).toBe(false);
  });
});

describe("demographic claim is earned, not assumed", () => {
  it("reports demographic context when the sections carry it", () => {
    const state = discoveryStateFor(
      { ...base, sections: { typical_day_in_this_year: 2 } },
      "1983-10-12"
    );
    expect(state.hasDemographicContext).toBe(true);
  });

  it("does not claim demographic context for an evidence-notes-only profile", () => {
    // context_only admits annual, period and evidence-note content alike,
    // so the tier alone cannot justify the sentence.
    const state = discoveryStateFor(
      { ...base, sections: { evidence_notes: 1 } },
      "1983-10-12"
    );
    expect(state.hasDemographicContext).toBe(false);
  });
});

describe("period context is not demographic context", () => {
  it("does not read armed-conflict context as demographic", () => {
    // wider_historical_context is any surrounding-period condition; UCDP
    // publishes armed-conflict counts there.
    const state = discoveryStateFor(
      { ...base, sections: { wider_historical_context: 3 } },
      "1983-10-12"
    );
    expect(state.hasDemographicContext).toBe(false);
    expect(state.hasPeriodContext).toBe(true);
  });

  it("reads the annual daily equivalents as demographic", () => {
    const state = discoveryStateFor(
      { ...base, sections: { typical_day_in_this_year: 2 } },
      "1983-10-12"
    );
    expect(state.hasDemographicContext).toBe(true);
  });
});

describe("the ordinary profile carries both kinds of context", () => {
  it("reports both when both sections are populated", () => {
    // The ordinary indexed context profile: two typical-day statements and
    // three wider-context ones.
    const state = discoveryStateFor(
      {
        ...base,
        sections: { typical_day_in_this_year: 2, wider_historical_context: 3 }
      },
      "1983-10-12"
    );
    expect(state.hasDemographicContext).toBe(true);
    expect(state.hasPeriodContext).toBe(true);
  });
});

describe("section counts are validated, not assumed", () => {
  it("rejects values that would coerce through a numeric comparison", () => {
    // true > 0 and "2" > 0 are both true in JavaScript, so an unvalidated
    // count renders a confident claim from a malformed response.
    for (const bad of [true, "2", null, 1.5, -1, {}] as unknown[]) {
      expect(
        isCoverageResponse(
          { ...base, sections: { typical_day_in_this_year: bad } },
          "1983-10-12"
        )
      ).toBe(false);
    }
  });

  it("rejects counts under keys outside the contract", () => {
    expect(
      isCoverageResponse(
        { ...base, sections: { recordedXonYthisZdate: 2 } },
        "1983-10-12"
      )
    ).toBe(false);
  });

  it("accepts real counts, including zero and an empty map", () => {
    expect(
      isCoverageResponse(
        { ...base, sections: { typical_day_in_this_year: 0, evidence_notes: 3 } },
        "1983-10-12"
      )
    ).toBe(true);
    expect(isCoverageResponse({ ...base, sections: {} }, "1983-10-12")).toBe(true);
  });
});

describe("discovery from an enriched page", () => {
  it("keeps its controls when other enriched dates exist", () => {
    const state = discoveryStateFor(
      {
        ...base,
        publication_tier: "enriched",
        review_status: "automated_only",
        quality_floor: "not_assessed",
        has_recorded_event: true,
        nearest_enriched_before: "1964-03-27"
      },
      "1983-10-12"
    );
    expect(state.kind).toBe("on-enriched-date");
    expect(state.hasAnyEnrichedDestination).toBe(true);
  });

  it("has nowhere to go from the only enriched date", () => {
    const state = discoveryStateFor(
      {
        ...base,
        publication_tier: "enriched",
        review_status: "automated_only",
        quality_floor: "not_assessed",
        has_recorded_event: true
      },
      "1964-03-27"
    );
    expect(state.kind).toBe("on-enriched-date");
    expect(state.hasAnyEnrichedDestination).toBe(false);
  });
});
