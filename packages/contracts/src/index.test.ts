import { describe, expect, it } from "vitest";

import type {
  NoEnrichedDatesResponse,
  RandomEnrichedResponse
} from "./index";
import { PUBLICATION_TIERS, profileTypeForDate } from "./index";

describe("profileTypeForDate", () => {
  it("keeps the public date shell and profile bands explicit", () => {
    expect(profileTypeForDate("1899-12-31")).toBeUndefined();
    expect(profileTypeForDate("1900-01-01")).toBe("limited_historical");
    expect(profileTypeForDate("1950-01-01")).toBe("standard_statistical");
    expect(profileTypeForDate("1989-01-01")).toBe("enhanced_structured");
    expect(profileTypeForDate("2026-01-01")).toBeUndefined();
  });
});


describe("publication tiers", () => {
  it("orders the vocabulary from sparse to rich", () => {
    expect(PUBLICATION_TIERS).toEqual([
      "context_only",
      "partially_enriched",
      "reviewed_enriched"
    ]);
  });
});

describe("random enriched discovery", () => {
  it("keeps the absence case explicit so the control can be hidden", () => {
    const found: RandomEnrichedResponse = {
      status: "enriched_date",
      date: "1964-03-27"
    };
    const absent: NoEnrichedDatesResponse = {
      status: "no_enriched_dates",
      detail: "No enriched dates are published."
    };
    expect(found.status).toBe("enriched_date");
    expect(absent.status).toBe("no_enriched_dates");
  });
});
