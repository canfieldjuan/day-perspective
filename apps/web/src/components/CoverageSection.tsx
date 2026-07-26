"use client";

import React, { useEffect, useState } from "react";

import type { CoverageDateResponse } from "@day-perspective/contracts";
import {
  discoveryStateFor,
  isCoverageResponse,
  type DiscoveryState
} from "@/src/lib/coverage";
import { describeSparsePage } from "@/src/lib/coverage-copy";
import { formatPublicDate } from "@/src/lib/date";
import { CoverageDiscovery } from "./CoverageDiscovery";

/**
 * Everything the coverage index has to say about this date.
 *
 * Fetched on the client rather than during the server render: coverage is
 * supplementary to the profile, so it must not delay the page, and a
 * request the browser makes is one an operator can see and a test can
 * intercept.
 */
export function CoverageSection({ date }: { date: string }) {
  // One piece of state keyed by date, so switching dates resets during
  // render rather than through a synchronous effect write.
  const [answer, setAnswer] = useState<{
    date: string;
    coverage: CoverageDateResponse | null;
  } | null>(null);

  useEffect(() => {
    let active = true;
    fetch(`/api/coverage/${encodeURIComponent(date)}`, {
      cache: "no-store",
      headers: { Accept: "application/json" }
    })
      .then(async (response) => (response.ok ? await response.json() : null))
      .then((payload: unknown) => {
        if (active) {
          setAnswer({
            date,
            coverage: isCoverageResponse(payload, date) ? payload : null
          });
        }
      })
      .catch(() => {
        if (active) {
          setAnswer({ date, coverage: null });
        }
      });
    return () => {
      active = false;
    };
  }, [date]);

  // Before the answer arrives — or while it belongs to the previous date —
  // say nothing. An unresolved request is not evidence of an empty archive.
  if (answer === null || answer.date !== date) {
    return null;
  }

  const coverage = answer.coverage;
  const state: DiscoveryState = discoveryStateFor(coverage, date);
  const monument = formatPublicDate(date) ?? date;

  return (
    <>
      {coverage?.publication_tier === "context_only" ? (
        <p className="coverage-tier" data-testid="publication-tier">
          {describeSparsePage(state, monument)}
        </p>
      ) : null}
      <CoverageDiscovery date={date} state={state} />
    </>
  );
}
