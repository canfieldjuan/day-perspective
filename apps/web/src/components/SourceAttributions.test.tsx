import { render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it } from "vitest";

import type { PublishedDayProfile } from "@day-perspective/contracts";
import { ProfileSections } from "./ProfileSections";

/**
 * A date whose recorded section rests on two publishers is not one publisher's
 * page. The singular `source_attribution` named whichever wrote last, so the
 * publisher stopped emitting it (D047) — which means this consumer has to read
 * the plural field in the same slice. A publisher that stops saying something
 * while its only reader still listens for the old thing does not remove a false
 * claim, it removes the provenance.
 */

const USGS = {
  name: "USGS earthquake catalog",
  publisher: "United States Geological Survey",
  url: "https://earthquake.usgs.gov/"
};
const WIKIDATA = {
  name: "Wikidata candidate entities",
  publisher: "Wikimedia Foundation",
  url: "https://www.wikidata.org/"
};

function renderIntegrity(profile: Partial<PublishedDayProfile>) {
  return render(
    <ProfileSections
      availability="published"
      sections={{ evidence_notes: [] }}
      sectionStates={{ evidence_notes: { status: "available" } }}
      sourceAttribution={profile.source_attribution}
      sourceAttributions={profile.source_attributions}
      profileDate="1964-03-27"
    />
  );
}

describe("a page names every source it rests on", () => {
  it("lists both publishers of a two-source date", () => {
    renderIntegrity({ source_attributions: [USGS, WIKIDATA] });

    expect(screen.getByText(USGS.name)).toBeTruthy();
    expect(screen.getByText(WIKIDATA.name)).toBeTruthy();
    expect(screen.getByText(/^Sources:/)).toBeTruthy();
    // Singular here would assert one publisher stands behind the whole page.
    expect(screen.queryByText(/^Source:/)).toBeNull();
  });

  it("reads naturally when there is genuinely one source", () => {
    renderIntegrity({ source_attributions: [USGS] });

    expect(screen.getByText(/^Source:/)).toBeTruthy();
    expect(screen.getByText(USGS.name)).toBeTruthy();
  });

  it("still renders profiles published before the plural field existed", () => {
    renderIntegrity({ source_attribution: USGS });

    expect(screen.getByText(/^Source:/)).toBeTruthy();
    expect(screen.getByText(USGS.name)).toBeTruthy();
  });

  it("does not fall back to the legacy field when the plural list is empty", () => {
    // Presence, not truthiness: an empty list means the evidence credits
    // nobody, and falling back would resurrect what the payload dropped.
    renderIntegrity({ source_attributions: [], source_attribution: WIKIDATA });

    expect(screen.queryByText(WIKIDATA.name)).toBeNull();
    expect(screen.queryByText(/^Sources?:/)).toBeNull();
  });

  it("links each source rather than naming it as plain text", () => {
    const { container } = renderIntegrity({
      source_attributions: [USGS, WIKIDATA]
    });

    const links = Array.from(
      container.querySelectorAll('[data-testid="source-attributions"] a')
    ).map((node) => node.getAttribute("href"));
    expect(links).toEqual([USGS.url, WIKIDATA.url]);
  });
});
