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

describe("ERA_BANDS", () => {
  it("tiles the public shell contiguously and agrees with the band mapping", async () => {
    const { ERA_BANDS, eraLineForDate } = await import("./day-profile");
    const { PUBLIC_DATE_MIN, PUBLIC_DATE_MAX } = await import(
      "@day-perspective/contracts"
    );
    expect(ERA_BANDS[0].start).toBe(PUBLIC_DATE_MIN);
    expect(ERA_BANDS[ERA_BANDS.length - 1].end).toBe(PUBLIC_DATE_MAX);
    for (const band of ERA_BANDS) {
      expect(eraLineForDate(band.start)).toBe(band.line);
      expect(eraLineForDate(band.end)).toBe(band.line);
    }
    for (let index = 1; index < ERA_BANDS.length; index += 1) {
      const previousEndYear = Number(ERA_BANDS[index - 1].end.slice(0, 4));
      const startYear = Number(ERA_BANDS[index].start.slice(0, 4));
      expect(startYear).toBe(previousEndYear + 1);
    }
  });
});

describe("publication tier validation", () => {
  function envelope(profileExtras: Record<string, unknown>) {
    return {
      status: "published",
      date: "1964-03-27",
      profile_type: "standard_statistical",
      manifest_id: "manifest-tier",
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

  it("accepts every tier in the contract vocabulary", () => {
    for (const tier of ["context_only", "partially_enriched", "enriched"]) {
      expect(
        isPublishedProfileResponse(
          envelope({ publication_tier: tier }),
          "1964-03-27"
        )
      ).toBe(true);
    }
  });

  it("rejects a tier outside the vocabulary instead of rendering it", () => {
    expect(
      isPublishedProfileResponse(
        envelope({ publication_tier: "fully_amazing" }),
        "1964-03-27"
      )
    ).toBe(false);
    expect(
      isPublishedProfileResponse(envelope({ publication_tier: 3 }), "1964-03-27")
    ).toBe(false);
  });
});

describe("event-group metadata at the response boundary", () => {
  const profileWith = (eventGroup: unknown) => ({
    status: "published",
    date: "1964-03-27",
    profile_type: "standard_statistical",
    manifest_id: "manifest-event-group",
    content_hash: "e".repeat(64),
    profile: {
      schema_version: "1",
      date: "1964-03-27",
      profile_type: "standard_statistical",
      sections: {
        recorded_on_this_date: [
          {
            statement_id: "s1",
            statement: "USGS names the record X.",
            ...(eventGroup === undefined ? {} : { event_group: eventGroup })
          }
        ]
      },
      section_states: {}
    }
  });

  const WELL_FORMED = {
    event_group_key: "group1",
    event_title: "1964 Alaska earthquake",
    featured: true,
    event_order: 0,
    predicate_order: 0
  };

  it("accepts a complete event group", () => {
    expect(
      isPublishedProfileResponse(profileWith(WELL_FORMED), "1964-03-27")
    ).toBe(true);
  });

  it("accepts a statement published before typed grouping existed", () => {
    expect(isPublishedProfileResponse(profileWith(undefined), "1964-03-27")).toBe(
      true
    );
  });

  it("rejects a null event group", () => {
    expect(isPublishedProfileResponse(profileWith(null), "1964-03-27")).toBe(
      false
    );
  });

  it("rejects an event group missing its key", () => {
    const withoutKey: Record<string, unknown> = { ...WELL_FORMED };
    delete withoutKey.event_group_key;
    expect(isPublishedProfileResponse(profileWith(withoutKey), "1964-03-27")).toBe(
      false
    );
  });

  it("rejects an event group whose order is not a number", () => {
    expect(
      isPublishedProfileResponse(
        profileWith({ ...WELL_FORMED, event_order: "0" }),
        "1964-03-27"
      )
    ).toBe(false);
  });

  it("rejects an event group whose featured flag is not a boolean", () => {
    expect(
      isPublishedProfileResponse(
        profileWith({ ...WELL_FORMED, featured: "true" }),
        "1964-03-27"
      )
    ).toBe(false);
  });
});

describe("empty event-group identifiers are not a usable group", () => {
  const envelopeWith = (eventGroup: unknown) => ({
    status: "published",
    date: "1964-03-27",
    profile_type: "standard_statistical",
    manifest_id: "manifest-empty-group",
    content_hash: "f".repeat(64),
    profile: {
      schema_version: "1",
      date: "1964-03-27",
      profile_type: "standard_statistical",
      sections: {
        recorded_on_this_date: [
          {
            statement_id: "s1",
            statement: "USGS names the record X.",
            event_group: eventGroup
          }
        ]
      },
      section_states: {}
    }
  });

  const COMPLETE = {
    event_group_key: "group1",
    event_title: "1964 Alaska earthquake",
    featured: true,
    event_order: 0,
    predicate_order: 0
  };

  it("rejects an empty group key", () => {
    // The renderer already treats this as unusable and drops grouping for the
    // whole section. Accepting it here turns a bad payload into a silently
    // ungrouped page instead of the API-error state.
    expect(
      isPublishedProfileResponse(
        envelopeWith({ ...COMPLETE, event_group_key: "" }),
        "1964-03-27"
      )
    ).toBe(false);
  });

  it("rejects an empty event title", () => {
    expect(
      isPublishedProfileResponse(
        envelopeWith({ ...COMPLETE, event_title: "" }),
        "1964-03-27"
      )
    ).toBe(false);
  });

  it("still accepts a group whose strings are present", () => {
    expect(
      isPublishedProfileResponse(envelopeWith(COMPLETE), "1964-03-27")
    ).toBe(true);
  });
});

describe("source attributions at the response boundary", () => {
  const envelope = (attributions: unknown) => ({
    status: "published",
    date: "1964-03-27",
    profile_type: "standard_statistical",
    manifest_id: "manifest-attributions",
    content_hash: "c".repeat(64),
    profile: {
      schema_version: "1",
      date: "1964-03-27",
      profile_type: "standard_statistical",
      sections: { evidence_notes: [] },
      section_states: {},
      source_attributions: attributions
    }
  });

  const COMPLETE = {
    name: "USGS earthquake catalog",
    publisher: "United States Geological Survey",
    url: "https://earthquake.usgs.gov/"
  };

  it("accepts a complete attribution list", () => {
    expect(isPublishedProfileResponse(envelope([COMPLETE]), "1964-03-27")).toBe(
      true
    );
  });

  it("accepts an empty list", () => {
    expect(isPublishedProfileResponse(envelope([]), "1964-03-27")).toBe(true);
  });

  it("accepts a source whose publisher and URL are genuinely unknown", () => {
    // Both columns are nullable upstream and the publisher emits "" for them.
    // Failing the profile here would discard a page of honest evidence because
    // one source has no recorded URL; the renderer degrades instead.
    expect(
      isPublishedProfileResponse(
        envelope([{ ...COMPLETE, publisher: "", url: "" }]),
        "1964-03-27"
      )
    ).toBe(true);
  });

  it("rejects an attribution with no name", () => {
    // `Source.name` is not nullable, so an empty name is a corrupt payload
    // rather than absent metadata.
    expect(
      isPublishedProfileResponse(
        envelope([{ ...COMPLETE, name: "" }]),
        "1964-03-27"
      )
    ).toBe(false);
  });

  it("rejects a non-string field", () => {
    expect(
      isPublishedProfileResponse(
        envelope([{ ...COMPLETE, url: 4 }]),
        "1964-03-27"
      )
    ).toBe(false);
  });

  it("rejects a list that is not a list", () => {
    expect(
      isPublishedProfileResponse(envelope({ ...COMPLETE }), "1964-03-27")
    ).toBe(false);
  });
});
