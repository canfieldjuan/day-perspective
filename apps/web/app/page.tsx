import { DateInputForm } from "@/src/components/DateInputForm";

export default function HomePage() {
  return (
    <main className="page-shell">
      <header className="masthead">
        <p className="eyebrow">Historical perspective</p>
        <h1>Look at one day with its evidence in view.</h1>
        <p className="lede">
          Choose a date from the public calendar shell. Published profiles distinguish
          recorded events, period context, calculations, and uncertainty.
        </p>
        <DateInputForm />
      </header>
    </main>
  );
}
