import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DayProfileClient } from "./DayProfileClient";
import { resetArrivalsForTests } from "@/src/lib/travel-store";

describe("DayProfileClient", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
    resetArrivalsForTests();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the unpublished state without replacing it with historical facts", async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(
        JSON.stringify({
          status: "profile_not_published",
          date: "1900-01-01",
          profile_type: "limited_historical",
          detail: "No profile has been published for this date yet."
        }),
        {
          headers: {
            "content-type": "application/json"
          },
          status: 404
        }
      )
    );

    render(<DayProfileClient date="1900-01-01" />);

    expect(
      await screen.findByRole("heading", {
        name: "This day does not have a published profile yet."
      })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", {
        name: "Recorded on this date"
      })
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        "/api/day/1900-01-01",
        expect.objectContaining({
          headers: {
            Accept: "application/json"
          }
        })
      );
    });
  });

  it("renders structured published statements only after validating the profile envelope", async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(
        JSON.stringify({
          status: "published",
          date: "1969-07-20",
          profile_type: "standard_statistical",
          manifest_id: "manifest-test",
          content_hash: "a".repeat(64),
          profile: {
            schema_version: "1",
            date: "1969-07-20",
            profile_type: "standard_statistical",
            sections: {
              recorded_on_this_date: [
                {
                  statement_id: "statement-test",
                  statement: "Synthetic statement used only to test structured rendering.",
                  provenance_note: "Test-only provenance note."
                }
              ],
              typical_day_in_this_year: [],
              wider_historical_context: [],
              curated_claims: [],
              derived_comparisons: [],
              wonder_and_progress: [],
              evidence_notes: []
            }
          }
        }),
        { headers: { "content-type": "application/json" }, status: 200 }
      )
    );

    render(<DayProfileClient date="1969-07-20" />);

    expect(
      await screen.findByText("Synthetic statement used only to test structured rendering.")
    ).toBeInTheDocument();
    expect(screen.getByText("Test-only provenance note.")).toBeInTheDocument();
  });

  it("renders the golden earthquake and opens its public provenance chain", async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(
        JSON.stringify({
          status: "published",
          date: "1964-03-27",
          profile_type: "standard_statistical",
          manifest_id: "manifest-golden",
          content_hash: "b".repeat(64),
          profile: {
            schema_version: "1",
            date: "1964-03-27",
            profile_type: "standard_statistical",
            sections: {
              recorded_on_this_date: [
                {
                  statement_id: "event-magnitude",
                  statement: "USGS reports a magnitude of 9.2 Mw.",
                  provenance: {
                    published_statement: "Selected.",
                    resolved_claim: {
                      canonical_key: "usgs:event:magnitude",
                      version: 1,
                      method: "single_source",
                      rationale: "Accepted official USGS record."
                    },
                    supporting_claims: [
                      {
                        predicate: "magnitude",
                        value: { value: 9.2, scale: "mw" },
                        source_record_locator: "https://earthquake.usgs.gov/example",
                        source_record_hash_sha256: "c".repeat(64)
                      }
                    ],
                    dissenting_claims: [],
                    source_release: {
                      source: "USGS Earthquake Catalog",
                      publisher: "U.S. Geological Survey",
                      release: "fixture-v1",
                      source_url: "https://earthquake.usgs.gov",
                      raw_checksum_sha256: "d".repeat(64),
                      retrieved_at: "2026-07-24T00:00:00Z"
                    },
                    methodology: {
                      name: "USGS authoritative resolution",
                      version: "1",
                      description: "Deterministic."
                    }
                  }
                }
              ],
              typical_day_in_this_year: [],
              wider_historical_context: [],
              curated_claims: [],
              derived_comparisons: [],
              wonder_and_progress: [],
              evidence_notes: []
            }
          }
        }),
        { headers: { "content-type": "application/json" }, status: 200 }
      )
    );

    render(<DayProfileClient date="1964-03-27" />);

    expect(
      await screen.findByText("USGS reports a magnitude of 9.2 Mw.")
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Why can the app say this?" })
    );
    expect(
      screen.getByRole("link", {
        name: "the USGS Earthquake Catalog source record"
      })
    ).toBeInTheDocument();
    expect(screen.getByText("None in this publication.")).toBeInTheDocument();
  });

  it("hides the previous profile immediately when the requested date changes", async () => {
    let resolveSecondRequest: (response: Response) => void = () => undefined;
    const secondRequest = new Promise<Response>((resolve) => {
      resolveSecondRequest = resolve;
    });
    vi.mocked(fetch)
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            status: "published",
            date: "1969-07-20",
            profile_type: "standard_statistical",
            manifest_id: "manifest-first-date",
            content_hash: "f".repeat(64),
            profile: {
              schema_version: "1",
              date: "1969-07-20",
              profile_type: "standard_statistical",
              sections: {
                recorded_on_this_date: [
                  {
                    statement_id: "first-date-statement",
                    statement: "Statement belonging only to the first date."
                  }
                ]
              }
            }
          }),
          { headers: { "content-type": "application/json" }, status: 200 }
        )
      )
      .mockReturnValueOnce(secondRequest);

    const { rerender } = render(<DayProfileClient date="1969-07-20" />);
    expect(
      await screen.findByText("Statement belonging only to the first date.")
    ).toBeInTheDocument();

    rerender(<DayProfileClient date="1900-01-01" />);

    expect(
      screen.queryByText("Statement belonging only to the first date.")
    ).not.toBeInTheDocument();
    expect(screen.getByText("Checking publication status")).toBeInTheDocument();

    resolveSecondRequest(
      new Response(
        JSON.stringify({
          status: "profile_not_published",
          date: "1900-01-01",
          profile_type: "limited_historical",
          detail: "No profile has been published for this date yet."
        }),
        { headers: { "content-type": "application/json" }, status: 404 }
      )
    );
    expect(
      await screen.findByRole("heading", {
        name: "This day does not have a published profile yet."
      })
    ).toBeInTheDocument();
  });

  it("rejects an unrecognized successful response instead of calling it published", async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(JSON.stringify({ status: "published" }), { status: 200 })
    );

    render(<DayProfileClient date="1969-07-20" />);

    expect(
      await screen.findByRole("heading", { name: "The profile could not be loaded." })
    ).toBeInTheDocument();
  });
});

describe("DayProfileClient arrival panel", () => {
  it("keeps the publication status inside the arrival panel", async () => {
    const { render: renderArrival, screen: arrivalScreen, waitFor } = await import(
      "@testing-library/react"
    ).then((m) => ({ render: m.render, screen: m.screen, waitFor: m.waitFor }));
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          status: "profile_not_published",
          date: "1900-01-01",
          profile_type: "limited_historical",
          detail: "No profile has been published for this date yet."
        }),
        { status: 404, headers: { "Content-Type": "application/json" } }
      )
    );
    try {
      renderArrival(
        <DayProfileClient
          date="1900-01-01"
          arrival={<h1>January 1, 1900</h1>}
        />
      );
      const arrival = arrivalScreen.getByTestId("day-arrival");
      expect(arrival).toHaveTextContent("January 1, 1900");
      await waitFor(() =>
        expect(arrival).toHaveTextContent("No profile is published for this date.")
      );
    } finally {
      fetchMock.mockRestore();
    }
  });
});

describe("DayProfileClient invalid-date variants", () => {
  it("tells a real out-of-range date apart from a malformed address", () => {
    const { unmount } = render(<DayProfileClient date="1899-12-31" />);
    expect(
      screen.getByRole("heading", { name: "This date is outside the public range." })
    ).toBeInTheDocument();
    expect(
      screen.getByText("Records span 1900-01-01 through 2025-12-31.")
    ).toBeInTheDocument();
    unmount();

    render(<DayProfileClient date="1964-02-30" />);
    expect(
      screen.getByRole("heading", { name: "This address is not a calendar date." })
    ).toBeInTheDocument();
    expect(
      screen.getByText("Use the form YYYY-MM-DD, between 1900-01-01 and 2025-12-31.")
    ).toBeInTheDocument();
  });
});

describe("DayProfileClient focus discipline", () => {
  it("does not re-steal focus when retrying the same date", async () => {
    const { markNavigation, resetArrivalsForTests } = await import(
      "@/src/lib/travel-store"
    );
    resetArrivalsForTests();
    markNavigation();
    const fetchMock = vi
      .spyOn(global, "fetch")
      .mockImplementation(async () =>
        new Response("{}", {
          status: 500,
          headers: { "Content-Type": "application/json" }
        })
      );

    render(
      <DayProfileClient date="1964-03-28" arrival={<h1 tabIndex={-1}>March 28, 1964</h1>} />
    );
    await screen.findByRole("heading", { name: "The profile could not be loaded." });
    expect(document.activeElement?.textContent).toBe("March 28, 1964");

    const retry = screen.getByRole("button", { name: "Retry profile request" });
    retry.focus();
    fireEvent.click(retry);
    await screen.findByRole("heading", { name: "The profile could not be loaded." });
    expect(document.activeElement?.textContent).not.toBe("March 28, 1964");
    fetchMock.mockRestore();
  });
});

describe("DayProfileClient first-load retry focus", () => {
  it("never moves focus to the heading when retrying without navigation", async () => {
    resetArrivalsForTests();
    const fetchMock = vi
      .spyOn(global, "fetch")
      .mockImplementation(async () =>
        new Response("{}", {
          status: 500,
          headers: { "Content-Type": "application/json" }
        })
      );

    render(
      <DayProfileClient
        date="1964-03-27"
        arrival={<h1 tabIndex={-1}>March 27, 1964</h1>}
      />
    );
    await screen.findByRole("heading", { name: "The profile could not be loaded." });
    expect(document.activeElement?.textContent).not.toBe("March 27, 1964");

    const retry = screen.getByRole("button", { name: "Retry profile request" });
    retry.focus();
    fireEvent.click(retry);
    await screen.findByRole("heading", { name: "The profile could not be loaded." });
    expect(document.activeElement?.textContent).not.toBe("March 27, 1964");
    fetchMock.mockRestore();
  });
});
