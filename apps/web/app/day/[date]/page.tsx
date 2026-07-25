import { DateInputForm } from "@/src/components/DateInputForm";
import { DayProfileClient } from "@/src/components/DayProfileClient";
import { formatPublicDate } from "@/src/lib/date";
import { eraLineForDate } from "@/src/lib/day-profile";

export default async function DayProfilePage({
  params
}: {
  params: Promise<{ date: string }>;
}) {
  const { date } = await params;
  const monument = formatPublicDate(date);
  const eraLine = eraLineForDate(date);

  return (
    <main className="page-shell">
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
    </main>
  );
}
