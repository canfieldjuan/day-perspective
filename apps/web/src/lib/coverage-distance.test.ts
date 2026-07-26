import { describe, expect, it } from "vitest";

import { describeDistance, distanceBand } from "./coverage-distance";

/**
 * The distance-language contract. The exact date is always the primary
 * information; this text is supporting. Never "soon", "nearby" or "just
 * ahead" — a date nineteen years away is not nearby, and saying so would
 * be the interface lying on the archive's behalf.
 */
describe("distanceBand", () => {
  it("separates the three bands at their real boundaries", () => {
    expect(distanceBand("1983-10-12", "1983-10-13")).toBe("days");
    expect(distanceBand("1983-10-12", "1983-11-12")).toBe("days"); // 31
    expect(distanceBand("1983-10-12", "1983-11-13")).toBe("months"); // 32
    expect(distanceBand("1983-10-12", "1984-10-10")).toBe("months"); // 364
    expect(distanceBand("1983-10-12", "1984-10-11")).toBe("years"); // 365
  });

  it("bands by magnitude, not direction", () => {
    expect(distanceBand("1983-10-12", "1983-10-07")).toBe("days");
    expect(distanceBand("1983-10-12", "1964-03-27")).toBe("years");
  });
});

describe("describeDistance", () => {
  it("gives exact days up to a month", () => {
    expect(describeDistance("1983-08-13", "1983-08-18")).toBe("five days later");
    expect(describeDistance("1964-03-26", "1964-03-27")).toBe("one day later");
    expect(describeDistance("1964-04-20", "1964-03-27")).toBe(
      "twenty-four days earlier"
    );
    expect(describeDistance("1983-10-12", "1983-11-12")).toBe(
      "thirty-one days later"
    );
  });

  it("approximates months between a month and a year, and says so", () => {
    expect(describeDistance("1983-08-27", "1984-03-27")).toBe(
      "about seven months later"
    );
    expect(describeDistance("1964-08-15", "1964-03-27")).toBe(
      "about five months earlier"
    );
    // 32 days is the first month-band value and must not read as "one month"
    // without the hedge, since it is not one.
    expect(describeDistance("1983-10-12", "1983-11-13")).toBe(
      "about one month later"
    );
  });

  it("uses whole years for the long jumps that dominate this archive", () => {
    expect(describeDistance("1983-10-12", "1964-03-27")).toBe(
      "nineteen years earlier"
    );
    expect(describeDistance("2025-12-31", "1964-03-27")).toBe(
      "sixty-one years earlier"
    );
    expect(describeDistance("1950-01-01", "1964-03-27")).toBe(
      "fourteen years later"
    );
  });

  it("adds months only where they clarify a short year-scale gap", () => {
    expect(describeDistance("1983-01-27", "1984-03-27")).toBe(
      "one year and two months later"
    );
    expect(describeDistance("1983-03-27", "1984-03-27")).toBe("one year later");
    // Beyond a couple of years the months stop being useful and start
    // being noise.
    expect(describeDistance("1980-01-27", "1984-03-27")).toBe(
      "four years later"
    );
  });

  it("never claims nearness for a distant date", () => {
    const distant = describeDistance("1983-10-12", "1964-03-27");
    for (const word of ["soon", "nearby", "just ahead", "shortly"]) {
      expect(distant).not.toContain(word);
    }
  });

  it("returns null when there is no distance to describe", () => {
    expect(describeDistance("1983-10-12", "1983-10-12")).toBeNull();
    expect(describeDistance("1983-10-12", "not-a-date")).toBeNull();
  });
});

describe("direction symmetry", () => {
  it("describes the same gap identically from either end", () => {
    const pairs: Array<[string, string]> = [
      ["1964-03-27", "1964-08-15"],
      ["1983-10-12", "1964-03-27"],
      ["1950-01-01", "2025-12-31"],
      ["1983-01-27", "1984-03-27"]
    ];
    for (const [a, b] of pairs) {
      const forward = describeDistance(a, b);
      const backward = describeDistance(b, a);
      expect(forward).not.toBeNull();
      const strip = (text: string | null) =>
        text?.replace(/ (later|earlier)$/, "") ?? null;
      expect(strip(forward)).toBe(strip(backward));
    }
  });
});

describe("band and count use compatible units", () => {
  it("never reports zero years inside the year band", () => {
    // 365 days, but only eleven whole calendar months.
    expect(distanceBand("2019-03-01", "2020-02-29")).toBe("years");
    expect(describeDistance("2019-03-01", "2020-02-29")).toBe("one year later");
    expect(describeDistance("2019-03-01", "2020-02-29")).not.toContain("zero");
  });

  it("keeps every year-band description free of a zero-year phrase", () => {
    const probes: Array<[string, string]> = [
      ["1983-10-12", "1984-10-11"],
      ["2019-03-01", "2020-02-29"],
      ["1964-03-27", "1965-03-26"],
      ["2000-02-29", "2001-02-28"]
    ];
    for (const [from, to] of probes) {
      if (distanceBand(from, to) !== "years") {
        continue;
      }
      expect(describeDistance(from, to)).not.toContain("zero");
    }
  });
});

describe("month-end anchors", () => {
  it("clamps rather than rolling a nonexistent date forward", () => {
    // January 31 plus one month is February 28, not March 3. Rolling over
    // moves the boundary the rounding is measured against.
    expect(describeDistance("1983-01-31", "1983-03-17")).toBe(
      "about two months later"
    );
    expect(describeDistance("1983-03-17", "1983-01-31")).toBe(
      "about two months earlier"
    );
  });

  it("stays correct across a leap-year month end", () => {
    expect(describeDistance("2020-01-31", "2020-02-29")).toBe(
      "twenty-nine days later"
    );
    expect(describeDistance("2019-01-31", "2019-04-15")).toBe(
      "about two months later"
    );
  });
});
