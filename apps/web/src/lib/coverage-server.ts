import type {
  CoverageDateResponse,
  CoverageSummaryResponse
} from "@day-perspective/contracts";

import { isCoverageResponse } from "./coverage";

function apiBaseUrl(): string {
  return (process.env.API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");
}

/**
 * Read one date's coverage on the server, or null when it cannot be read.
 *
 * Null covers three different situations deliberately — unindexed date,
 * unreachable service, malformed answer — because the interface treats all
 * three the same way: it says nothing about the archive rather than
 * guessing. Only a validated record produces a claim.
 */
export async function fetchCoverage(
  date: string
): Promise<CoverageDateResponse | null> {
  try {
    const response = await fetch(
      `${apiBaseUrl()}/api/v1/coverage/${encodeURIComponent(date)}`,
      { cache: "no-store", headers: { Accept: "application/json" } }
    );
    if (!response.ok) {
      return null;
    }
    const payload: unknown = await response.json();
    return isCoverageResponse(payload, date) ? payload : null;
  } catch {
    return null;
  }
}


function isSummary(payload: unknown): payload is CoverageSummaryResponse {
  const record =
    payload && typeof payload === "object" && !Array.isArray(payload)
      ? (payload as Record<string, unknown>)
      : undefined;
  return (
    record !== undefined &&
    record.status === "coverage_summary" &&
    typeof record.total_published === "number" &&
    typeof record.with_recorded_event === "number"
  );
}

/** The archive's shape, or null when it cannot be read. */
export async function fetchCoverageSummary(): Promise<CoverageSummaryResponse | null> {
  try {
    const response = await fetch(`${apiBaseUrl()}/api/v1/coverage`, {
      cache: "no-store",
      headers: { Accept: "application/json" }
    });
    if (!response.ok) {
      return null;
    }
    const payload: unknown = await response.json();
    return isSummary(payload) ? payload : null;
  } catch {
    return null;
  }
}
