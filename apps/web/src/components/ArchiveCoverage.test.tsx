import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { CoverageSummaryResponse } from "@day-perspective/contracts";

import { ArchiveCoverage } from "./ArchiveCoverage";

const summary: CoverageSummaryResponse = {
  status: "coverage_summary",
  total_published: 27759,
  by_tier: { context_only: 27758, partially_enriched: 0, enriched: 1 },
  with_recorded_event: 1,
  earliest: "1950-01-01",
  latest: "2025-12-31",
  supported_range: { minimum: "1900-01-01", maximum: "2025-12-31" }
};

describe("ArchiveCoverage", () => {
  it("discloses the archive's real shape rather than implying uniform richness", () => {
    render(<ArchiveCoverage summary={summary} />);

    expect(
      screen.getByText(
        "27,759 dates are published, from January 1, 1950 to December 31, 2025."
      )
    ).toBeInTheDocument();
    expect(
      screen.getByText(/One carries a recorded event\./)
    ).toBeInTheDocument();
    // Only what the aggregates prove: the tier breakdown, not a claim
    // about what those dates contain.
    expect(
      screen.getByText(/27,758 carry the annual context of their year/)
    ).toBeInTheDocument();
  });

  it("says plainly when no date carries a recorded event", () => {
    render(
      <ArchiveCoverage summary={{ ...summary, with_recorded_event: 0 }} />
    );
    expect(
      screen.getByText(/No date yet carries a recorded event\./)
    ).toBeInTheDocument();
  });

  it("pluralises once the archive holds more than one", () => {
    render(
      <ArchiveCoverage summary={{ ...summary, with_recorded_event: 1234 }} />
    );
    expect(
      screen.getByText(/1,234 carry recorded events\./)
    ).toBeInTheDocument();
  });

  it("says nothing at all when coverage is unknown", () => {
    render(<ArchiveCoverage summary={null} />);
    // An unreadable summary must not render as an empty archive.
    expect(screen.queryByTestId("archive-coverage")).toBeNull();
  });

  it("says nothing when the archive is genuinely empty", () => {
    render(
      <ArchiveCoverage summary={{ ...summary, total_published: 0 }} />
    );
    expect(screen.queryByTestId("archive-coverage")).toBeNull();
  });
});
