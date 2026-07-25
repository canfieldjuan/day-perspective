/**
 * Travel choreography phases (UI_UX_CONTRACT C-7). Pure derivations from
 * the client view state: the traveling phase lasts exactly as long as the
 * real request — motion may accompany loading but never gates content.
 */
export type TravelPhase = "traveling" | "arrived";
export type ArrivalEntry = "initial" | "adjacent";

export function phaseForView(
  viewKind: "loading" | "unpublished" | "api-error" | "published"
): TravelPhase {
  return viewKind === "loading" ? "traveling" : "arrived";
}

/** First arrival gets the full staged reveal; later date changes stay quick. */
export function entryKindForArrival(hasArrivedBefore: boolean): ArrivalEntry {
  return hasArrivedBefore ? "adjacent" : "initial";
}
