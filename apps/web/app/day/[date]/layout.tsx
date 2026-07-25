import type { ReactNode } from "react";

import { DayNavigation } from "@/src/components/DayNavigation";

/**
 * Segment layout so the date navigation survives the page's loading and
 * error fallbacks (UI_UX_CONTRACT C-6.1: day-nav on every /day/* render).
 */
export default async function DayLayout({
  children,
  params
}: {
  children: ReactNode;
  params: Promise<{ date: string }>;
}) {
  const { date } = await params;

  return (
    <main className="page-shell" id="main-content">
      <div className="day-layout">
        <div className="day-layout__content">{children}</div>
        <DayNavigation date={date} />
      </div>
    </main>
  );
}
