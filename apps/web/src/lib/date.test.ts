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

describe("adjacentPublicDate", () => {
  it("steps forward and backward across ordinary days", async () => {
    const { adjacentPublicDate } = await import("./date");
    expect(adjacentPublicDate("1964-03-27", 1)).toBe("1964-03-28");
    expect(adjacentPublicDate("1964-03-27", -1)).toBe("1964-03-26");
  });

  it("crosses month and year boundaries", async () => {
    const { adjacentPublicDate } = await import("./date");
    expect(adjacentPublicDate("1964-03-31", 1)).toBe("1964-04-01");
    expect(adjacentPublicDate("1964-01-01", -1)).toBe("1963-12-31");
    expect(adjacentPublicDate("1963-12-31", 1)).toBe("1964-01-01");
  });

  it("handles leap years — 1964 has February 29, 1900 does not", async () => {
    const { adjacentPublicDate } = await import("./date");
    expect(adjacentPublicDate("1964-02-28", 1)).toBe("1964-02-29");
    expect(adjacentPublicDate("1964-02-29", 1)).toBe("1964-03-01");
    expect(adjacentPublicDate("1900-02-28", 1)).toBe("1900-03-01");
    expect(adjacentPublicDate("2000-02-28", 1)).toBe("2000-02-29");
  });

  it("clamps to null at the shell edges instead of wrapping", async () => {
    const { adjacentPublicDate } = await import("./date");
    expect(adjacentPublicDate("1900-01-01", -1)).toBeNull();
    expect(adjacentPublicDate("2025-12-31", 1)).toBeNull();
    expect(adjacentPublicDate("1900-01-01", 1)).toBe("1900-01-02");
    expect(adjacentPublicDate("2025-12-31", -1)).toBe("2025-12-30");
  });

  it("returns null for unsupported input", async () => {
    const { adjacentPublicDate } = await import("./date");
    expect(adjacentPublicDate("1964-3-27", 1)).toBeNull();
    expect(adjacentPublicDate("garbage", 1)).toBeNull();
  });
});

describe("randomPublicDate", () => {
  it("always lands inside the shell in canonical form", async () => {
    const { randomPublicDate, isSupportedPublicDate } = await import("./date");
    for (let i = 0; i < 200; i += 1) {
      const date = randomPublicDate();
      expect(isSupportedPublicDate(date)).toBe(true);
    }
  });

  it("can reach both shell edges given controlled randomness", async () => {
    const { randomPublicDate } = await import("./date");
    expect(randomPublicDate(() => 0)).toBe("1900-01-01");
    expect(randomPublicDate(() => 0.9999999)).toBe("2025-12-31");
  });
});

describe("canonicalizePublicDatePath", () => {
  it("pads parseable non-canonical dates", async () => {
    const { canonicalizePublicDatePath } = await import("./date");
    expect(canonicalizePublicDatePath("1964-3-27")).toBe("1964-03-27");
    expect(canonicalizePublicDatePath("1964-03-7")).toBe("1964-03-07");
    expect(canonicalizePublicDatePath("1900-1-1")).toBe("1900-01-01");
  });

  it("returns null for already-canonical input", async () => {
    const { canonicalizePublicDatePath } = await import("./date");
    expect(canonicalizePublicDatePath("1964-03-27")).toBeNull();
  });

  it("returns null for real-but-out-of-shell or invalid values", async () => {
    const { canonicalizePublicDatePath } = await import("./date");
    expect(canonicalizePublicDatePath("1899-12-31")).toBeNull();
    expect(canonicalizePublicDatePath("1964-02-30")).toBeNull();
    expect(canonicalizePublicDatePath("not-a-date")).toBeNull();
    expect(canonicalizePublicDatePath("1964-003-27")).toBeNull();
  });
});
