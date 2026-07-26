import type { Metadata } from "next";

import { CoverageDiscovery } from "@/src/components/CoverageDiscovery";
import { EnrichedNavigation } from "@/src/components/EnrichedNavigation";
import { DateInputForm } from "@/src/components/DateInputForm";
import { DayProfileClient } from "@/src/components/DayProfileClient";
import { discoveryStateFor } from "@/src/lib/coverage";
import { fetchCoverage } from "@/src/lib/coverage-server";
import { formatPublicDate } from "@/src/lib/date";
import { eraLineForDate } from "@/src/lib/day-profile";

type DayPageProps = { params: Promise<{ date: string }> };

export async function generateMetadata({ params }: DayPageProps): Promise<Metadata> {
  const { date } = await params;
  const monument = formatPublicDate(date);
  if (!monument) {
    return { title: "Day Perspective" };
  }
  return {
    title: monument + " — Day Perspective",
    description:
      "Evidence-led historical profile of " +
      monument +
      " (" +
      eraLineForDate(date) +
      ")."
  };
}

export default async function DayProfilePage({ params }: DayPageProps) {
  const { date } = await params;
  const monument = formatPublicDate(date);
  const eraLine = eraLineForDate(date);
  // Coverage indexes only published dates, so it is the authority on how
  // rich this page is. An unreadable answer says nothing rather than
  // implying emptiness.
  const coverage = await fetchCoverage(date);
  const discovery = discoveryStateFor(coverage, date);
  const contextOnly = coverage?.publication_tier === "context_only";

  return (
    <DayProfileClient
      date={date}
      arrival={
        <>
          <p className="eyebrow">Historical perspective</p>
          <h1 tabIndex={-1}>{monument ?? "Day profile: " + date}</h1>
          {eraLine ? <p className="day-arrival__era">{eraLine}</p> : null}
          {contextOnly ? (
            <p className="day-arrival__tier" data-testid="publication-tier">
              This date currently has demographic context only. No reviewed
              recorded events are published for {monument ?? date}.
            </p>
          ) : null}
          <DateInputForm initialDate={date} />
        </>
      }
      discovery={<CoverageDiscovery date={date} state={discovery} />}
      enrichedNavigation={
        <EnrichedNavigation date={date} state={discovery} />
      }
    />
  );
}
