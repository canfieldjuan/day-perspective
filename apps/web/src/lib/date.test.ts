import { describe, expect, it } from "vitest";

import { formatPublicDate } from "./date";

describe("formatPublicDate", () => {
  it("writes a canonical date out as a monument string", () => {
    expect(formatPublicDate("1964-03-27")).toBe("March 27, 1964");
    expect(formatPublicDate("1900-01-01")).toBe("January 1, 1900");
    expect(formatPublicDate("2025-12-31")).toBe("December 31, 2025");
  });

  it("does not pad the day number", () => {
    expect(formatPublicDate("1950-07-04")).toBe("July 4, 1950");
  });

  it("returns null for anything that is not a supported canonical date", () => {
    expect(formatPublicDate("1964-3-27")).toBeNull();
    expect(formatPublicDate("1899-12-31")).toBeNull();
    expect(formatPublicDate("2026-01-01")).toBeNull();
    expect(formatPublicDate("1964-02-30")).toBeNull();
    expect(formatPublicDate("not-a-date")).toBeNull();
  });
});
