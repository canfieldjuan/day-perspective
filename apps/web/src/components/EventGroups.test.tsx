import { render, screen, within } from "@testing-library/react";
import React from "react";
import { describe, expect, it } from "vitest";

import type {
  ProfileStatement,
  PublishedDayProfile
} from "@day-perspective/contracts";
import { ProfileSections } from "./ProfileSections";

/**
 * A date can hold more than one recorded event once a human has adjudicated
 * them distinct, and exactly one of them leads (D046). The payload now says
 * which statement belongs to which event (D047), so the page can group without
 * inferring anything from array position.
 *
 * What these tests protect is the reader's ability to tell the events apart. A
 * flat run of eight statements — four about an earthquake, four about a
 * different earthquake — is not a page a person can read; they would have to
 * work out where one event ends by noticing the sentences changed subject.
 */

const statement = (
  id: string,
  text: string,
  group: ProfileStatement["event_group"]
): ProfileStatement => ({
  statement_id: id,
  statement: text,
  event_group: group,
  provenance: {
    root_type: "resolved_claim",
    published_statement: "Selected for the recorded-event section.",
    resolved_claim: {
      canonical_key: `key:${id}`,
      version: 1,
      method: "single_source",
      rationale: "Single reviewed candidate."
    },
    supporting_claims: [
      {
        predicate: "occurrence",
        value: { value: "1964-03-27" },
        source_record_locator: `https://example.invalid/${id}`,
        source_record_hash_sha256: "a".repeat(64)
      }
    ],
    dissenting_claims: [],
    source_release: {
      source: "Fixture source",
      publisher: "Fixture publisher",
      release: "fixture-1",
      source_url: "https://example.invalid/",
      raw_checksum_sha256: "b".repeat(64),
      retrieved_at: "2026-07-24T00:00:00Z"
    },
    methodology: {
      name: "Fixture methodology",
      version: "1",
      description: "Test-only."
    }
  }
});

const FEATURED = {
  event_group_key: "featured01",
  event_title: "1964 Alaska earthquake",
  featured: true,
  event_order: 0
};

const SECONDARY = {
  event_group_key: "secondary1",
  event_title: "A second recorded event",
  featured: false,
  event_order: 1
};

function multiEventProfile(): PublishedDayProfile {
  return {
    schema_version: "1",
    date: "1964-03-27",
    profile_type: "standard_statistical",
    sections: {
      recorded_on_this_date: [
        statement("featured-name", "Wikidata records this event as X.", {
          ...FEATURED,
          predicate_order: 0
        }),
        statement("featured-date", "Wikidata records the occurrence on X.", {
          ...FEATURED,
          predicate_order: 1
        }),
        statement("secondary-title", "USGS names the record X.", {
          ...SECONDARY,
          predicate_order: 0
        }),
        statement("secondary-magnitude", "USGS reports a magnitude of X.", {
          ...SECONDARY,
          predicate_order: 1
        })
      ]
    },
    section_states: { recorded_on_this_date: { status: "available" } },
    source_attributions: [
      {
        name: "USGS earthquake catalog",
        publisher: "United States Geological Survey",
        url: "https://earthquake.usgs.gov/"
      },
      {
        name: "Wikidata candidate entities",
        publisher: "Wikimedia Foundation",
        url: "https://www.wikidata.org/"
      }
    ]
  };
}

function singleEventProfile(): PublishedDayProfile {
  return {
    schema_version: "1",
    date: "1964-03-27",
    profile_type: "standard_statistical",
    sections: {
      recorded_on_this_date: [
        statement("only-name", "USGS names the record X.", {
          ...FEATURED,
          predicate_order: 0
        }),
        statement("only-magnitude", "USGS reports a magnitude of X.", {
          ...FEATURED,
          predicate_order: 1
        })
      ]
    },
    section_states: { recorded_on_this_date: { status: "available" } },
    source_attributions: [
      {
        name: "USGS earthquake catalog",
        publisher: "United States Geological Survey",
        url: "https://earthquake.usgs.gov/"
      }
    ]
  };
}

function renderProfile(profile: PublishedDayProfile) {
  return render(
    <ProfileSections
      availability="published"
      sections={profile.sections}
      sectionStates={profile.section_states}
      sourceAttributions={profile.source_attributions}
      profileDate={profile.date}
    />
  );
}

describe("recorded events are grouped for a reader", () => {
  it("separates the featured event from the others", () => {
    renderProfile(multiEventProfile());

    const featured = screen.getByTestId("event-group-featured01");
    const secondary = screen.getByTestId("event-group-secondary1");

    expect(
      within(featured).getByText("Wikidata records this event as X.")
    ).toBeTruthy();
    expect(
      within(featured).getByText("Wikidata records the occurrence on X.")
    ).toBeTruthy();
    // A statement from the other event must not appear inside this group.
    expect(
      within(featured).queryByText("USGS names the record X.")
    ).toBeNull();
    expect(within(secondary).getByText("USGS names the record X.")).toBeTruthy();
  });

  it("names each event so a reader knows what they are looking at", () => {
    renderProfile(multiEventProfile());

    expect(screen.getByText("1964 Alaska earthquake")).toBeTruthy();
    expect(screen.getByText("A second recorded event")).toBeTruthy();
  });

  it("marks the secondary events as also recorded rather than as the headline", () => {
    renderProfile(multiEventProfile());

    expect(screen.getByText("Also recorded on this date")).toBeTruthy();
  });

  it("renders the featured event before the others", () => {
    const { container } = renderProfile(multiEventProfile());

    const groups = Array.from(
      container.querySelectorAll("[data-event-group]")
    ).map((node) => node.getAttribute("data-event-group"));
    expect(groups).toEqual(["featured01", "secondary1"]);
  });

  it("gives the lead emphasis to the featured event, not merely to the first statement", () => {
    const { container } = renderProfile(multiEventProfile());

    const featured = container.querySelector('[data-event-group="featured01"]');
    const secondary = container.querySelector('[data-event-group="secondary1"]');
    expect(featured?.getAttribute("data-featured")).toBe("true");
    expect(secondary?.getAttribute("data-featured")).toBe("false");
  });

  it("keeps every statement's evidence reachable inside its group", () => {
    renderProfile(multiEventProfile());

    // One provenance control per statement, not one per page or one per group:
    // a reader asking "why can the app say this?" is asking about a sentence.
    expect(screen.getAllByRole("button", { name: /why can the app say this/i })
      .length).toBe(4);
  });
});

describe("a single-event date reads as it always did", () => {
  it("does not announce a secondary section when there is only one event", () => {
    renderProfile(singleEventProfile());

    expect(screen.queryByText("Also recorded on this date")).toBeNull();
  });

  it("still renders its statements and their evidence controls", () => {
    renderProfile(singleEventProfile());

    expect(screen.getByText("USGS names the record X.")).toBeTruthy();
    expect(screen.getByText("USGS reports a magnitude of X.")).toBeTruthy();
    expect(
      screen.getAllByRole("button", { name: /why can the app say this/i }).length
    ).toBe(2);
  });
});

describe("attribution does not collapse several sources into one", () => {
  it("names every source a multi-source page rests on", () => {
    renderProfile(multiEventProfile());

    expect(screen.getByText("USGS earthquake catalog")).toBeTruthy();
    expect(screen.getByText("Wikidata candidate entities")).toBeTruthy();
  });

  it("does not present one source as supporting the whole page", () => {
    renderProfile(multiEventProfile());

    // "Source:" singular would assert that one publisher stands behind
    // everything on the page, which is exactly the claim the payload stopped
    // making.
    expect(screen.queryByText(/^Source:/)).toBeNull();
    expect(screen.getByText(/^Sources:/)).toBeTruthy();
  });

  it("still reads naturally when there is genuinely one source", () => {
    renderProfile(singleEventProfile());

    expect(screen.getByText(/^Source:/)).toBeTruthy();
    expect(screen.getByText("USGS earthquake catalog")).toBeTruthy();
  });
});

describe("grouping stays navigable", () => {
  it("nests each event title one level under its section heading", () => {
    const { container } = renderProfile(multiEventProfile());

    // A screen-reader user moves by heading level. Jumping h2 -> h4 for the
    // featured group would leave a hole in the outline, and the featured event
    // is the one most likely to be jumped to.
    const levels = Array.from(
      container.querySelectorAll("h1, h2, h3, h4, h5, h6")
    ).map((node) => Number(node.tagName.slice(1)));
    for (let index = 1; index < levels.length; index += 1) {
      expect(levels[index] - levels[index - 1]).toBeLessThanOrEqual(1);
    }
  });

  it("exposes both event titles as headings", () => {
    renderProfile(multiEventProfile());

    expect(
      screen.getByRole("heading", { name: "1964 Alaska earthquake" })
    ).toBeTruthy();
    expect(
      screen.getByRole("heading", { name: "A second recorded event" })
    ).toBeTruthy();
  });

  it("keeps the secondary label out of the heading outline", () => {
    renderProfile(multiEventProfile());

    // Present for a sighted reader as a divider, but not a heading competing
    // with the event titles it introduces.
    expect(screen.getByText("Also recorded on this date")).toBeTruthy();
    expect(
      screen.queryByRole("heading", { name: "Also recorded on this date" })
    ).toBeNull();
  });
});

describe("malformed or partial grouping degrades instead of breaking", () => {
  it("renders flat when a statement carries a null event group", () => {
    const profile = multiEventProfile();
    // A payload can carry null where a group is expected. Guarding only
    // `undefined` lets it through to a property access that throws, and a
    // published profile turns into a blank page.
    (profile.sections.recorded_on_this_date as ProfileStatement[])[2] = {
      ...(profile.sections.recorded_on_this_date as ProfileStatement[])[2],
      event_group: null as unknown as ProfileStatement["event_group"]
    };

    const { container } = renderProfile(profile);

    expect(container.querySelectorAll("[data-event-group]").length).toBe(0);
    // Every statement is still on the page; only the grouping is withheld.
    expect(screen.getByText("Wikidata records this event as X.")).toBeTruthy();
    expect(screen.getByText("USGS names the record X.")).toBeTruthy();
  });

  it("renders flat when a group is missing its fields", () => {
    const profile = multiEventProfile();
    (profile.sections.recorded_on_this_date as ProfileStatement[])[0] = {
      ...(profile.sections.recorded_on_this_date as ProfileStatement[])[0],
      event_group: { featured: true } as unknown as ProfileStatement["event_group"]
    };

    const { container } = renderProfile(profile);

    expect(container.querySelectorAll("[data-event-group]").length).toBe(0);
    expect(screen.getByText("Wikidata records this event as X.")).toBeTruthy();
  });

  it("orders secondary events by their published order, not by the opaque key", () => {
    const profile = multiEventProfile();
    const third = {
      event_group_key: "aaa-sorts-first",
      event_title: "A third recorded event",
      featured: false,
      event_order: 2
    };
    // The keys are chosen so alphabetical order and published order disagree:
    // "aaa-sorts-first" precedes "secondary1", but the API published it second.
    (profile.sections.recorded_on_this_date as ProfileStatement[]).push(
      statement("third-title", "A third source names the record X.", {
        ...third,
        predicate_order: 0
      })
    );

    const { container } = renderProfile(profile);

    const groups = Array.from(
      container.querySelectorAll("[data-event-group]")
    ).map((node) => node.getAttribute("data-event-group"));
    expect(groups).toEqual(["featured01", "secondary1", "aaa-sorts-first"]);
  });
});
