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

describe("isPublishedProfileResponse optional metadata", () => {
  function envelope(profileExtras: Record<string, unknown>) {
    return {
      status: "published",
      date: "1964-03-27",
      profile_type: "standard_statistical",
      manifest_id: "manifest-metadata",
      content_hash: "a".repeat(64),
      profile: {
        schema_version: "1",
        date: "1964-03-27",
        profile_type: "standard_statistical",
        sections: { evidence_notes: [] },
        ...profileExtras
      }
    };
  }

  it("accepts well-formed quality and source attribution", () => {
    expect(
      isPublishedProfileResponse(
        envelope({
          quality: { grade: "B", explanation: "Single validated source." },
          source_attribution: {
            name: "USGS Earthquake Catalog",
            publisher: "U.S. Geological Survey",
            url: "https://earthquake.usgs.gov"
          }
        }),
        "1964-03-27"
      )
    ).toBe(true);
  });

  it("rejects malformed quality instead of letting it reach render", () => {
    expect(
      isPublishedProfileResponse(
        envelope({ quality: { grade: "B", explanation: {} } }),
        "1964-03-27"
      )
    ).toBe(false);
    expect(
      isPublishedProfileResponse(envelope({ quality: "B" }), "1964-03-27")
    ).toBe(false);
  });

  it("rejects malformed source attribution", () => {
    expect(
      isPublishedProfileResponse(
        envelope({ source_attribution: { name: "USGS", publisher: null, url: 4 } }),
        "1964-03-27"
      )
    ).toBe(false);
  });
});

describe("eraLineForDate calendar validity", () => {
  it("refuses impossible dates that are lexically in range", async () => {
    const { eraLineForDate } = await import("./day-profile");
    expect(eraLineForDate("1964-02-30")).toBeNull();
    expect(eraLineForDate("1900-99-99")).toBeNull();
  });
});
