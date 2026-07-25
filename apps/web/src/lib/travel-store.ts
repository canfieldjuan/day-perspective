/**
 * Arrival counter that survives soft navigations: App Router remounts the
 * page subtree per dynamic-segment param, so per-mount state cannot tell a
 * first journey from an adjacent-date step. Module state in the client
 * bundle persists across those remounts. Server renders only the traveling
 * phase, so per-request module state cannot leak into markup.
 */
let arrivals = 0;
let navigated = false;

export function recordArrival(): number {
  arrivals += 1;
  return arrivals;
}

/**
 * User-initiated navigation marks intent at the interaction layer, so an
 * arrival that interrupted (and aborted) a pending first request still
 * counts as an adjacent step, and React strict-mode double effects cannot
 * inflate it — clicks are not double-invoked.
 */
export function markNavigation(): void {
  navigated = true;
}

export function hasNavigated(): boolean {
  return navigated;
}

export function resetArrivalsForTests(): void {
  arrivals = 0;
  navigated = false;
}
