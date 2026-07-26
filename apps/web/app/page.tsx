import { PUBLIC_DATE_MAX, PUBLIC_DATE_MIN } from "@day-perspective/contracts";

import { ArchiveCoverage } from "@/src/components/ArchiveCoverage";
import { DateInputForm } from "@/src/components/DateInputForm";
import { fetchCoverageSummary } from "@/src/lib/coverage-server";
import { ERA_BANDS } from "@/src/lib/day-profile";

function bandYears(band: { start: string; end: string }): number {
  return Number(band.end.slice(0, 4)) - Number(band.start.slice(0, 4)) + 1;
}

export default async function HomePage() {
  // Disclosed rather than implied: nearly every published date holds
  // period context and not a recorded event.
  const summary = await fetchCoverageSummary();
  return (
    <main className="page-shell" id="main-content">
      <header className="masthead landing-masthead">
        <p className="eyebrow">Historical perspective</p>
        <h1 className="landing-title">Stand inside one day of the record.</h1>
        <p className="lede">
          Choose a date between {PUBLIC_DATE_MIN.slice(0, 4)} and{" "}
          {PUBLIC_DATE_MAX.slice(0, 4)}. A published profile keeps
          recorded events, period context, calculations, and uncertainty
          visibly separate — and says plainly what the evidence cannot
          support.
        </p>
      </header>
      <section aria-label="Supported eras" className="era-horizon" data-testid="era-horizon">
        <div className="era-horizon__band" role="presentation">
          {ERA_BANDS.map((band) => (
            <div
              className="era-horizon__segment"
              data-era={band.key}
              key={band.key}
              style={{ flex: bandYears(band) + " 1 0" }}
            >
              <span className="era-horizon__label">{band.line}</span>
            </div>
          ))}
        </div>
        <DateInputForm />
      </section>
      <ArchiveCoverage summary={summary} />
    </main>
  );
}
