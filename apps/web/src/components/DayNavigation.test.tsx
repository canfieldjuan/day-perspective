import { render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() })
}));

import { DayNavigation } from "./DayNavigation";

describe("DayNavigation", () => {
  it("labels previous and next with their actual target dates", () => {
    render(<DayNavigation date="1964-03-27" />);
    const nav = screen.getByRole("navigation", { name: "Date navigation" });
    expect(nav).toHaveAttribute("data-testid", "day-nav");
    expect(
      screen.getByRole("link", { name: "Previous day, March 26, 1964" })
    ).toHaveAttribute("href", "/day/1964-03-26");
    expect(
      screen.getByRole("link", { name: "Next day, March 28, 1964" })
    ).toHaveAttribute("href", "/day/1964-03-28");
  });

  it("omits the edge direction at the shell boundaries instead of wrapping", () => {
    render(<DayNavigation date="1900-01-01" />);
    expect(
      screen.queryByRole("link", { name: /Previous day/ })
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Next day, January 2, 1900" })
    ).toBeInTheDocument();
  });

  it("omits both steps when the reference date is invalid, keeping recovery actions", () => {
    render(<DayNavigation date="not-a-date" />);
    expect(screen.queryByRole("link", { name: /Previous day/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Next day/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Random day" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Another date" })).toBeInTheDocument();
  });

  it("offers the change-date recovery as an anchor to the date form", () => {
    render(<DayNavigation date="1964-03-27" />);
    expect(screen.getByRole("link", { name: "Another date" })).toHaveAttribute(
      "href",
      "#historical-date"
    );
  });
});
