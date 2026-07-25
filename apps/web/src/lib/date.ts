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

/** Step one day in either direction; null when unsupported or past a shell edge. */
export function adjacentPublicDate(
  value: string,
  offsetDays: 1 | -1
): string | null {
  if (!isSupportedPublicDate(value)) {
    return null;
  }
  const parsed = new Date(value + "T00:00:00.000Z");
  parsed.setUTCDate(parsed.getUTCDate() + offsetDays);
  const stepped = parsed.toISOString().slice(0, 10);
  return isSupportedPublicDate(stepped) ? stepped : null;
}

const SHELL_DAY_MS = 86_400_000;
const SHELL_START_MS = Date.parse(PUBLIC_DATE_MIN + "T00:00:00.000Z");
const SHELL_END_MS = Date.parse(PUBLIC_DATE_MAX + "T00:00:00.000Z");
const SHELL_DAY_COUNT = (SHELL_END_MS - SHELL_START_MS) / SHELL_DAY_MS + 1;

/** Uniform draw over the public shell; `random` is injectable for tests. */
export function randomPublicDate(random: () => number = Math.random): string {
  const index = Math.min(
    SHELL_DAY_COUNT - 1,
    Math.floor(random() * SHELL_DAY_COUNT)
  );
  return new Date(SHELL_START_MS + index * SHELL_DAY_MS)
    .toISOString()
    .slice(0, 10);
}

/**
 * Zero-pad a parseable non-canonical date path into the canonical supported
 * form. Null when already canonical, out of shell, or not a real date —
 * callers redirect only on a non-null result.
 */
export function canonicalizePublicDatePath(value: string): string | null {
  const match = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(value);
  if (!match) {
    return null;
  }
  const padded =
    match[1] + "-" + match[2].padStart(2, "0") + "-" + match[3].padStart(2, "0");
  if (padded === value) {
    return null;
  }
  return isSupportedPublicDate(padded) ? padded : null;
}

export type PublicDateInputClass = "supported" | "out-of-range" | "malformed";

/**
 * Distinguish a real calendar date outside the shell from a value that is
 * not a calendar date at all (UI_UX_CONTRACT C-8.2 renders them
 * differently — the old single message misled about in-range malformed
 * values, audit §3.4).
 */
export function classifyPublicDateInput(value: string): PublicDateInputClass {
  if (isSupportedPublicDate(value)) {
    return "supported";
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return "malformed";
  }
  const parsed = new Date(value + "T00:00:00.000Z");
  const isRealDate =
    !Number.isNaN(parsed.getTime()) &&
    parsed.toISOString().slice(0, 10) === value;
  return isRealDate ? "out-of-range" : "malformed";
}
