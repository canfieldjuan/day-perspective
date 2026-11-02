import { act, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CoverageDateResponse } from "@day-perspective/contracts";
import { CoverageSection } from "./CoverageSection";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const base: CoverageDateResponse = {
  status: "coverage",
  date: "1983-10-12",
  profile_type: "standard_statistical",
  publication_tier: "context_only",
  review_status: "automated_only",
  quality_floor: "not_assessed",
  has_recorded_event: false,
  sections: { typical_day_in_this_year: 2 },
  nearest_enriched_before: null,
  nearest_enriched_after: null,
  nearest_recorded_event_before: null,
  nearest_recorded_event_after: null
};

function response(payload: unknown, ok = true) {
  return { ok, json: async () => payload } as Response;
}

afterEach(() => vi.unstubAllGlobals());

describe("coverage review and evidence metadata", () => {
  const reviews = [
    ["automated_only", "Automated checks only"],
    ["review_pending", "Review pending"],
    ["human_reviewed", "Human reviewed"]
  ] as const;

  for (const [review_status, label] of reviews) {
    it.each(["A", "B", "C", "D", "not_assessed"] as const)(
      `shows ${review_status} independently of evidence quality %s`,
      async (quality_floor) => {
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
          ...base,
          publication_tier: "enriched",
          has_recorded_event: true,
          review_status,
          quality_floor
        })));
        render(<CoverageSection date={base.date} />);
        const metadata = await screen.findByRole("region", {
          name: "Review and evidence quality"
        });
        expect(within(metadata).getByText("Review status")).toBeVisible();
        expect(within(metadata).getByText(label)).toBeVisible();
        expect(within(metadata).getByText("Evidence quality")).toBeVisible();
        expect(within(metadata).getByText(
          quality_floor === "not_assessed" ? "Not assessed" : `Grade ${quality_floor}`
        )).toBeVisible();
        expect(within(metadata).getByText("Weakest published evidence.")).toBeVisible();
        // Enriched dates suppress discovery, but must still disclose metadata.
        expect(screen.queryByTestId("coverage-discovery")).not.toBeInTheDocument();
      }
    );
  }

  it.each(["context_only", "partially_enriched"] as const)(
    "also discloses metadata on a %s date",
    async (publication_tier) => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ ...base, publication_tier })));
      render(<CoverageSection date={base.date} />);
      expect(await screen.findByText("Automated checks only")).toBeVisible();
      expect(screen.getByText("Not assessed")).toBeVisible();
    }
  );

  it.each([
    ["unavailable", {}, false],
    ["unknown review", { ...base, review_status: "approved" }, true],
    ["missing quality", { ...base, quality_floor: undefined }, true],
    ["unknown quality", { ...base, quality_floor: "A+" }, true],
    ["wrong date", { ...base, date: "1983-10-13" }, true]
  ])("does not invent metadata for %s coverage", async (_, payload, ok) => {
    const fetchMock = vi.fn().mockResolvedValue(response(payload, ok as boolean));
    vi.stubGlobal("fetch", fetchMock);
    await act(async () => { render(<CoverageSection date={base.date} />); });
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(screen.queryByRole("region", { name: "Review and evidence quality" }))
      .not.toBeInTheDocument();
  });

  it("clears old metadata on date change and ignores a late response", async () => {
    let finishOld!: (value: Response) => void;
    const old = new Promise<Response>((resolve) => { finishOld = resolve; });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(base))
      .mockReturnValueOnce(old)
      .mockResolvedValueOnce(response({ ...base, date: "1983-10-14", review_status: "human_reviewed", quality_floor: "D" }));
    vi.stubGlobal("fetch", fetchMock);
    const { rerender } = render(<CoverageSection date={base.date} />);
    await screen.findByText("Automated checks only");
    rerender(<CoverageSection date="1983-10-13" />);
    expect(screen.queryByText("Automated checks only")).not.toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    rerender(<CoverageSection date="1983-10-14" />);
    await screen.findByText("Human reviewed");
    await act(async () => { finishOld(response({ ...base, date: "1983-10-13" })); });
    expect(screen.getByText("Human reviewed")).toBeVisible();
    expect(screen.getByText("Grade D")).toBeVisible();
    expect(screen.queryByText("Automated checks only")).not.toBeInTheDocument();
  });
});
