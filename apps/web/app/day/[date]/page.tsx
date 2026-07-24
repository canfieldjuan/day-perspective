import { DateInputForm } from "@/src/components/DateInputForm";
import { DayProfileClient } from "@/src/components/DayProfileClient";

export default async function DayProfilePage({
  params
}: {
  params: Promise<{ date: string }>;
}) {
  const { date } = await params;

  return (
    <main className="page-shell">
      <header className="masthead">
        <p className="eyebrow">Historical perspective</p>
        <h1>Day profile: {date}</h1>
        <p className="lede">
          Each section remains separate so recorded evidence, period-level context, and
          later comparisons cannot be mistaken for the same kind of statement.
        </p>
        <DateInputForm initialDate={date} />
      </header>
      <DayProfileClient date={date} />
    </main>
  );
}
