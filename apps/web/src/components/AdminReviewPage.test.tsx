import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor
} from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ReviewPage from "@/app/admin/review/page";

const release = {
  release_id: "release-a", label: "Fixture event", entity_id: "Q100", revision: 7,
  source_url: "https://www.wikidata.org/wiki/Q100?oldid=7", checksum: "fixture-checksum",
  claims: [{ id: "claim-a", predicate: "candidate_name", status: "candidate",
    value: { value: { label: "Fixture event" }, wikidata_reference_count: 0 }, locator: "record-7" }],
  can_resolve: false, can_publish: false, preview: [], blocked: null
};

function enterDate(day: string) {
  fireEvent.change(screen.getByLabelText("Occurrence date"), { target: { value: day } });
  fireEvent.click(screen.getByRole("button", { name: "Load candidates" }));
}

describe("development review console", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("loads Wikidata candidates for the chosen occurrence date", async () => {
    const request = vi.fn(async () => Response.json({ candidates: [] }));
    vi.stubGlobal("fetch", request);
    render(<ReviewPage />);
    fireEvent.change(screen.getByLabelText("Development review token"), {
      target: { value: "local-test-token" }
    });
    fireEvent.change(screen.getByLabelText("Occurrence date"), {
      target: { value: "1964-03-27" }
    });
    fireEvent.click(screen.getByRole("button", { name: "Load candidates" }));
    await screen.findByText("No matching imported candidates for this date.");
    expect(request).toHaveBeenCalledWith("/api/admin/wikidata/1964-03-27", expect.anything());
  });

  it("ignores a late response after the date changes", async () => {
    let complete!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => { complete = resolve; });
    vi.stubGlobal("fetch", vi.fn((url: string) => url.endsWith("1964-03-27")
      ? pending : Promise.resolve(Response.json({ candidates: [] }))));
    render(<ReviewPage />);
    enterDate("1964-03-27");
    enterDate("1964-03-28");
    await screen.findByText("No matching imported candidates for this date.");
    await act(async () => complete(Response.json({ candidates: [release] })));
    expect(screen.queryByLabelText("Source release")).not.toBeInTheDocument();
  });

  it("blocks duplicate decisions and refreshes a refused stale claim", async () => {
    let complete!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => { complete = resolve; });
    const refreshed = { ...release, claims: [{ ...release.claims[0], status: "accepted" }] };
    let submitted = false;
    const request = vi.fn((url: string, init?: RequestInit) => {
      if (init?.method === "POST") { submitted = true; return pending; }
      return Promise.resolve(Response.json({ candidates: [submitted ? refreshed : release] }));
    });
    vi.stubGlobal("fetch", request);
    render(<ReviewPage />);
    enterDate("1964-03-27");
    fireEvent.change(await screen.findByLabelText("Source release"), { target: { value: "release-a" } });
    expect(screen.getByText("No references recorded.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: release.source_url })).toHaveAttribute("href", release.source_url);
    fireEvent.change(screen.getByLabelText("Rationale for name"), { target: { value: "Checked source" } });
    const accept = screen.getByRole("button", { name: "Accept name" });
    fireEvent.click(accept);
    fireEvent.click(accept);
    expect(accept).toBeDisabled();
    expect(screen.getByLabelText("Occurrence date")).toBeDisabled();
    expect(screen.getByLabelText("Development review token")).toBeDisabled();
    const posts = request.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(posts).toHaveLength(1);
    expect(posts[0][0]).toBe("/api/admin/wikidata/1964-03-27/release-a/claims/claim-a/decision");
    await act(async () => complete(Response.json({ detail: "Claim was already reviewed." }, { status: 409 })));
    expect(await screen.findByText("accepted")).toBeInTheDocument();
    expect(screen.getByText(/Claim was already reviewed/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept name" })).not.toBeInTheDocument();
  });

  it("labels the guard as development-only and not secure authentication", () => {
    render(<ReviewPage />);

    expect(
      screen.getByRole("heading", { name: "Development review console" })
    ).toBeInTheDocument();
    expect(screen.getByText(/not secure authentication/i)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Load review state" })
    ).toBeInTheDocument();
  });

  it("only offers the generic resolution action for supported releases", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const path = String(input);
        const body = path.endsWith("/releases")
          ? {
              releases: [
                {
                  release_id: "usgs-release",
                  release_label: "USGS fixture",
                  source_slug: "usgs-earthquake-catalog",
                  resolution_supported: true,
                  claim_statuses: ["accepted"]
                },
                {
                  release_id: "wpp-release",
                  release_label: "UN WPP fixture",
                  source_slug: "un-wpp-2024",
                  resolution_supported: false,
                  claim_statuses: ["accepted"]
                }
              ]
            }
          : {};
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { "content-type": "application/json" }
        });
      })
    );
    render(<ReviewPage />);

    fireEvent.change(screen.getByLabelText("Development review token"), {
      target: { value: "development-only-change-me" }
    });
    fireEvent.click(screen.getByRole("button", { name: "Load review state" }));

    await screen.findByText("USGS fixture");
    expect(screen.getByText("UN WPP fixture")).toBeInTheDocument();
    expect(
      screen.getAllByRole("button", { name: "Resolve accepted claims" })
    ).toHaveLength(1);
    await waitFor(() => {
      expect(
        screen.getByText(/specific review workflow/i)
      ).toBeInTheDocument();
    });
  });
});
