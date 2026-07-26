import React from "react";

import type { CoverageSummaryResponse } from "@day-perspective/contracts";
import { formatPublicDate } from "@/src/lib/date";

/**
 * What the archive actually holds, disclosed on the landing page.
 *
 * Every supported date carries demographic context, and almost none carry
 * a reviewed recorded event. A landing page that implied otherwise would
 * set an expectation the archive cannot meet, and the reader would find
 * out one identical page at a time.
 */
export function ArchiveCoverage({
  summary
}: {
  summary: CoverageSummaryResponse | null;
}) {
  // Nothing known is not the same as nothing published; say nothing.
  if (summary === null || summary.total_published === 0) {
    return null;
  }

  const enriched = summary.with_recorded_event;
  const earliest = summary.earliest ? formatPublicDate(summary.earliest) : null;
  const latest = summary.latest ? formatPublicDate(summary.latest) : null;

  return (
    <section
      aria-label="Archive coverage"
      className="archive-coverage"
      data-testid="archive-coverage"
    >
      <p>
        {summary.total_published.toLocaleString("en-US")} dates are published
        {earliest && latest ? `, from ${earliest} to ${latest}` : ""}. Each
        carries the reviewed demographic context of its year.
      </p>
      <p className="archive-coverage__caveat">
        {enriched === 0
          ? "No date yet carries a reviewed recorded event."
          : enriched === 1
            ? "One of them also carries a reviewed recorded event."
            : `${enriched.toLocaleString("en-US")} of them also carry reviewed recorded events.`}{" "}
        The rest hold period context rather than something that happened on
        that day.
      </p>
    </section>
  );
}
