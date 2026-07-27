import { describe, expect, it } from "vitest";

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
      "enriched"
    ]);
  });
});
