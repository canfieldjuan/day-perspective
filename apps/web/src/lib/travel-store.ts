/**
 * Arrival counter that survives soft navigations: App Router remounts the
 * page subtree per dynamic-segment param, so per-mount state cannot tell a
 * first journey from an adjacent-date step. Module state in the client
 * bundle persists across those remounts. Server renders only the traveling
 * phase, so per-request module state cannot leak into markup.
 */
let arrivals = 0;

export function recordArrival(): number {
  arrivals += 1;
  return arrivals;
}

export function resetArrivalsForTests(): void {
  arrivals = 0;
}
