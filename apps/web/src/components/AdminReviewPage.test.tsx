import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor
} from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ReviewPage from "@/app/admin/review/page";

describe("development review console", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
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
