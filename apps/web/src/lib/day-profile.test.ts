import { describe, expect, it } from "vitest";

import { DAY_PROFILE_SECTION_KEYS } from "@day-perspective/contracts";
import { isPublishedProfileResponse } from "./day-profile";

describe("isPublishedProfileResponse", () => {
  it("accepts a valid profile with a partial sections map", () => {
    expect(
      isPublishedProfileResponse(
        {
          status: "published",
          date: "1964-03-27",
          profile_type: "standard_statistical",
          manifest_id: "manifest-partial-sections",
          content_hash: "a".repeat(64),
          profile: {
            schema_version: "1",
            date: "1964-03-27",
            profile_type: "standard_statistical",
            sections: {
              evidence_notes: []
            },
            section_states: {}
          }
        },
        "1964-03-27"
      )
    ).toBe(true);
  });

  it("rejects malformed section-state reasons before rendering", () => {
    const sections = Object.fromEntries(
      DAY_PROFILE_SECTION_KEYS.map((key) => [key, []])
    );

    expect(
      isPublishedProfileResponse(
        {
          status: "published",
          date: "1964-03-27",
          profile_type: "standard_statistical",
          manifest_id: "manifest-section-state",
          content_hash: "d".repeat(64),
          profile: {
            schema_version: "1",
            date: "1964-03-27",
            profile_type: "standard_statistical",
            sections,
            section_states: {
              recorded_on_this_date: {
                status: "not_yet_supported",
                reason: { unsafe: "React child" }
              }
            }
          }
        },
        "1964-03-27"
      )
    ).toBe(false);
  });
});

describe("eraLineForDate", () => {
  it("names each era band with its canonical range", async () => {
    const { eraLineForDate } = await import("./day-profile");
    expect(eraLineForDate("1900-01-01")).toBe("Limited historical era · 1900–1949");
    expect(eraLineForDate("1964-03-27")).toBe("Standard statistical era · 1950–1988");
    expect(eraLineForDate("1989-01-01")).toBe("Enhanced structured era · 1989–2025");
  });

  it("returns null outside the public shell", async () => {
    const { eraLineForDate } = await import("./day-profile");
    expect(eraLineForDate("1899-12-31")).toBeNull();
    expect(eraLineForDate("garbage")).toBeNull();
  });
});
