import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() })
}));

import { DateInputForm } from "./DateInputForm";
import { hasNavigated, resetArrivalsForTests } from "@/src/lib/travel-store";

describe("DateInputForm navigation intent", () => {
  beforeEach(() => {
    resetArrivalsForTests();
  });

  function submitDate(value: string) {
    fireEvent.change(screen.getByLabelText("Date"), { target: { value } });
    fireEvent.click(screen.getByRole("button", { name: "Open profile" }));
  }

  it("does not mark navigation from the landing form — first journeys stay initial", () => {
    render(<DateInputForm />);
    submitDate("1964-03-27");
    expect(hasNavigated()).toBe(false);
  });

  it("marks navigation when changing the date from an open day page", () => {
    render(<DateInputForm initialDate="1964-03-27" />);
    submitDate("1964-03-28");
    expect(hasNavigated()).toBe(true);
  });
});
