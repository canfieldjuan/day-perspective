import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DayProfileClient } from "./DayProfileClient";

describe("DayProfileClient", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
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
              ]
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
