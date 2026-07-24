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
    expect(
      screen.getByText("Why can the app say this?")
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", {
        name: "the USGS Earthquake Catalog source record"
      })
    ).toBeInTheDocument();
    expect(screen.getByText("None in this publication.")).toBeInTheDocument();
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
