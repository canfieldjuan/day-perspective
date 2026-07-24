import { render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it } from "vitest";
import ReviewPage from "@/app/admin/review/page";

describe("development review console", () => {
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
});
