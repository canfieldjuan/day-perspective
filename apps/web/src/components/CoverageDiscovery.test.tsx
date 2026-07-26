import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { CoverageDateResponse } from "@day-perspective/contracts";

import { CoverageDiscovery } from "./CoverageDiscovery";
import { discoveryStateFor } from "@/src/lib/coverage";

const base: CoverageDateResponse = {
  status: "coverage",
  date: "1983-10-12",
  profile_type: "standard_statistical",
  publication_tier: "context_only",
  has_recorded_event: false,
  sections: {},
  nearest_enriched_before: null,
  nearest_enriched_after: null,
  nearest_recorded_event_before: null,
  nearest_recorded_event_after: null
};

function renderFor(
  overrides: Partial<CoverageDateResponse>,
  date = "1983-10-12"
) {
  const state = discoveryStateFor({ ...base, ...overrides, date }, date);
  render(<CoverageDiscovery date={date} state={state} />);
  return state;
}

describe("CoverageDiscovery", () => {
  it("labels the group so the two navigation families are distinguishable", () => {
    renderFor({ nearest_enriched_before: "1964-03-27" });
    expect(
      screen.getByRole("heading", { name: "Find reviewed evidence" })
    ).toBeInTheDocument();
  });

  it("states a distant destination plainly, without claiming nearness", () => {
    renderFor({ nearest_enriched_before: "1964-03-27" });

    expect(
      screen.getByText("The closest reviewed date is farther away")
    ).toBeInTheDocument();
    expect(
      screen.getByText("March 27, 1964, nineteen years earlier.")
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Jump to March 27, 1964" })
    ).toHaveAttribute("href", "/day/1964-03-27");

    const panel = screen.getByTestId("coverage-discovery");
    for (const word of ["soon", "nearby", "just ahead"]) {
      expect(panel.textContent?.toLowerCase()).not.toContain(word);
    }
  });

  it("explains the direction that has nothing rather than showing a bare disabled arrow", () => {
    renderFor({ nearest_enriched_before: "1964-03-27" });
    expect(
      screen.getByText(
        "No reviewed enriched date is currently published after October 12, 1983."
      )
    ).toBeInTheDocument();
  });

  it("invites a genuinely close date differently", () => {
    renderFor(
      {
        nearest_enriched_after: "1964-03-27",
        nearest_recorded_event_after: "1964-03-27"
      },
      "1964-03-26"
    );

    expect(screen.getByText("Continue to a richer date")).toBeInTheDocument();
    expect(
      screen.getByText("March 27, 1964 has reviewed recorded events, one day later.")
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Explore March 27, 1964" })
    ).toBeInTheDocument();
  });

  it("does not promise recorded events a nearby destination does not hold", () => {
    // nearest_enriched_* spans partially_enriched, which carries curated or
    // comparison content and no recorded event.
    renderFor(
      {
        nearest_enriched_after: "1964-03-27",
        nearest_recorded_event_after: null
      },
      "1964-03-26"
    );

    expect(
      screen.getByText(
        "March 27, 1964 carries more than annual context, one day later."
      )
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/has reviewed recorded events/)
    ).toBeNull();
  });

  it("offers both directions without preselecting either", () => {
    renderFor({
      nearest_enriched_before: "1975-09-03",
      nearest_enriched_after: "1984-03-27"
    });

    expect(screen.getByText("Closest reviewed dates")).toBeInTheDocument();
    expect(
      screen.getByText("September 3, 1975 · eight years earlier")
    ).toBeInTheDocument();
    expect(
      screen.getByText("March 27, 1984 · about five months later")
    ).toBeInTheDocument();

    const earlier = screen.getByRole("link", { name: "Go earlier" });
    const later = screen.getByRole("link", { name: "Go later" });
    expect(earlier).toHaveAttribute("href", "/day/1975-09-03");
    expect(later).toHaveAttribute("href", "/day/1984-03-27");
    // Keyboard order must genuinely reach the closer destination first;
    // asserting the attribute alone proved nothing about focus order.
    expect(later).toHaveAttribute("data-closer", "true");
    expect(earlier).not.toHaveAttribute("data-closer");
    const ordered = screen.getAllByRole("link");
    expect(ordered.indexOf(later)).toBeLessThan(ordered.indexOf(earlier));
  });

  it("puts the closer destination first when the earlier one is closer", () => {
    renderFor({
      nearest_enriched_before: "1983-09-30",
      nearest_enriched_after: "1990-01-01"
    });
    const ordered = screen.getAllByRole("link");
    const earlier = screen.getByRole("link", { name: "Go earlier" });
    const later = screen.getByRole("link", { name: "Go later" });
    expect(ordered.indexOf(earlier)).toBeLessThan(ordered.indexOf(later));
    expect(earlier).toHaveAttribute("data-closer", "true");
  });

  it("says the index is empty only when it is", () => {
    renderFor({});
    expect(
      screen.getByText(/No reviewed enriched dates are currently available/)
    ).toBeInTheDocument();
  });

  it("renders nothing on an enriched page, which is not sparse", () => {
    renderFor({
      publication_tier: "reviewed_enriched",
      has_recorded_event: true
    });
    expect(screen.queryByTestId("coverage-discovery")).toBeNull();
  });

  it("renders nothing when coverage could not be read", () => {
    render(
      <CoverageDiscovery
        date="1983-10-12"
        state={discoveryStateFor(null, "1983-10-12")}
      />
    );
    // Unknown coverage must not be rendered as an empty archive.
    expect(screen.queryByTestId("coverage-discovery")).toBeNull();
  });
});
