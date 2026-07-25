import type { Metadata } from "next";

import { DateInputForm } from "@/src/components/DateInputForm";
import { DayProfileClient } from "@/src/components/DayProfileClient";
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

  return (
    <DayProfileClient
      date={date}
      arrival={
        <>
          <p className="eyebrow">Historical perspective</p>
          <h1>{monument ?? "Day profile: " + date}</h1>
          {eraLine ? <p className="day-arrival__era">{eraLine}</p> : null}
          <DateInputForm initialDate={date} />
        </>
      }
    />
  );
}
