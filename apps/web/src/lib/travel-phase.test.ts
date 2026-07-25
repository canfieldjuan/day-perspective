import { describe, expect, it } from "vitest";

import { entryKindForArrival, phaseForView } from "./travel-phase";

describe("phaseForView", () => {
  it("marks the pending request as traveling and everything else as arrived", () => {
    expect(phaseForView("loading")).toBe("traveling");
    expect(phaseForView("published")).toBe("arrived");
    expect(phaseForView("unpublished")).toBe("arrived");
    expect(phaseForView("api-error")).toBe("arrived");
  });
});

describe("entryKindForArrival", () => {
  it("treats the first arrival as initial and later ones as adjacent", () => {
    expect(entryKindForArrival(false)).toBe("initial");
    expect(entryKindForArrival(true)).toBe("adjacent");
  });
});
