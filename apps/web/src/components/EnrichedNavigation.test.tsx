import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() })
}));

import type { CoverageDateResponse } from "@day-perspective/contracts";

import { EnrichedNavigation } from "./EnrichedNavigation";
import { discoveryStateFor } from "@/src/lib/coverage";

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

function renderFor(
  overrides: Partial<CoverageDateResponse>,
  options: { random?: () => Promise<string | null> } = {}
) {
  const date = overrides.date ?? "1983-10-12";
  const state = discoveryStateFor({ ...base, ...overrides, date }, date);
  render(
    <EnrichedNavigation
      date={date}
      state={state}
      resolveRandom={options.random}
    />
  );
}

describe("EnrichedNavigation", () => {
  it("is a navigation family of its own, not part of the date bar", () => {
    renderFor({ nearest_enriched_before: "1964-03-27" });
    expect(
      screen.getByRole("navigation", { name: "Evidence discovery" })
    ).toBeInTheDocument();
  });

  it("names the destination and its distance in the accessible label", () => {
    renderFor({ nearest_enriched_before: "1964-03-27" });
    expect(
      screen.getByRole("link", {
        name: "Previous enriched date, March 27, 1964, nineteen years earlier"
      })
    ).toHaveAttribute("href", "/day/1964-03-27");
  });

  it("explains the direction with nothing instead of leaving a silent gap", () => {
    renderFor({ nearest_enriched_before: "1964-03-27" });
    expect(screen.queryByRole("link", { name: /Next enriched date/ })).toBeNull();
    expect(screen.getByTestId("enriched-nav-absence")).toHaveTextContent(
      "No enriched date is currently published after October 12, 1983."
    );
  });

  it("offers both directions when both exist", () => {
    renderFor({
      nearest_enriched_before: "1975-09-03",
      nearest_enriched_after: "1984-03-27"
    });
    expect(
      screen.getByRole("link", { name: /Previous enriched date/ })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /Next enriched date/ })
    ).toBeInTheDocument();
    expect(screen.getByTestId("enriched-nav-absence")).toHaveTextContent("");
  });

  it("renders nothing at all when no enriched destination is reachable", () => {
    renderFor({}, { random: async () => null });
    // A button that leads nowhere is not an affordance.
    expect(screen.queryByTestId("enriched-nav")).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Random enriched date" })
    ).toBeNull();
  });

  it("offers the random control by default wherever discovery is possible", () => {
    // The resolver is imported by the component, so the control is present
    // whenever there is anywhere to go — and absent entirely when there is
    // not, which the previous case covers.
    renderFor({ nearest_enriched_before: "1964-03-27" });
    expect(
      screen.getByRole("button", { name: "Random enriched date" })
    ).toBeInTheDocument();
  });

  it("offers a random enriched date as a distinct action when one exists", () => {
    renderFor(
      { nearest_enriched_before: "1964-03-27" },
      { random: async () => "1964-03-27" }
    );
    const random = screen.getByRole("button", { name: "Random enriched date" });
    expect(random).toBeInTheDocument();
    // It must not be confusable with the chronological random control.
    expect(screen.queryByRole("button", { name: "Random day" })).toBeNull();
  });
});
