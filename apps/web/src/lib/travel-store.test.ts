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

  it("remembers user navigation independently of completed arrivals", async () => {
    const { hasNavigated, markNavigation } = await import("./travel-store");
    resetArrivalsForTests();
    expect(hasNavigated()).toBe(false);
    markNavigation();
    expect(hasNavigated()).toBe(true);
    resetArrivalsForTests();
    expect(hasNavigated()).toBe(false);
  });
});
