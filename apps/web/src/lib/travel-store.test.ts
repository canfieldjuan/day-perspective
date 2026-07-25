import { describe, expect, it } from "vitest";

import { recordArrival, resetArrivalsForTests } from "./travel-store";

describe("travel store", () => {
  it("counts arrivals monotonically and resets for tests", () => {
    resetArrivalsForTests();
    expect(recordArrival()).toBe(1);
    expect(recordArrival()).toBe(2);
    resetArrivalsForTests();
    expect(recordArrival()).toBe(1);
  });
});
