import React from "react";

import type { CoverageSummaryResponse } from "@day-perspective/contracts";
import { describeArchiveShape } from "@/src/lib/coverage-copy";

/**
 * What the archive actually holds, disclosed on the landing page.
 *
 * Every supported date carries demographic context, and almost none carry
 * a recorded event. A landing page that implied otherwise would
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

  const { scale, caveat } = describeArchiveShape(summary);

  return (
    <section
      aria-label="Archive coverage"
      className="archive-coverage"
      data-testid="archive-coverage"
    >
      <p>{scale}</p>
      <p className="archive-coverage__caveat">{caveat}</p>
    </section>
  );
}
