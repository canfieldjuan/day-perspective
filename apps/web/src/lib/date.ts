import { PUBLIC_DATE_MAX, PUBLIC_DATE_MIN } from "@day-perspective/contracts";

export { PUBLIC_DATE_MAX, PUBLIC_DATE_MIN };

const MONTH_NAMES = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December"
] as const;

/** "1964-03-27" -> "March 27, 1964"; null unless a supported canonical date. */
export function formatPublicDate(value: string): string | null {
  if (!isSupportedPublicDate(value)) {
    return null;
  }
  const month = MONTH_NAMES[Number(value.slice(5, 7)) - 1];
  const day = Number(value.slice(8, 10));
  const year = value.slice(0, 4);
  return `${month} ${day}, ${year}`;
}

export function isSupportedPublicDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return false;
  }

  const parsed = new Date(value + "T00:00:00.000Z");

  return (
    !Number.isNaN(parsed.getTime()) &&
    parsed.toISOString().slice(0, 10) === value &&
    value >= PUBLIC_DATE_MIN &&
    value <= PUBLIC_DATE_MAX
  );
}
