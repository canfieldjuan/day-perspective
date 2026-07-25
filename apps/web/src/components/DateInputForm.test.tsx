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

describe("DateInputForm live era feedback", () => {
  beforeEach(() => {
    resetArrivalsForTests();
  });

  it("keeps the live region mounted but empty until the user edits", () => {
    render(<DateInputForm initialDate="1964-03-27" />);
    expect(screen.getByTestId("era-live")).toHaveTextContent("");
  });

  it("announces the typed date's era after an edit", () => {
    render(<DateInputForm />);
    fireEvent.change(screen.getByLabelText("Date"), {
      target: { value: "1900-06-15" }
    });
    expect(screen.getByTestId("era-live")).toHaveTextContent(
      "Limited historical era · 1900–1949"
    );
  });
});
