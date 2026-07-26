import { isSupportedPublicDate } from "./date";

/**
 * The distance-language contract for evidence discovery.
 *
 * The exact destination date is always the primary information; this text
 * supports it. Distances in this archive are usually decades rather than
 * days, so the language must stay accurate at that scale: never "soon",
 * "nearby" or "just ahead" for a jump measured in years.
 */
export type DistanceBand = "days" | "months" | "years";

const DAY_MS = 24 * 60 * 60 * 1000;

const ONES = [
  "zero",
  "one",
  "two",
  "three",
  "four",
  "five",
  "six",
  "seven",
  "eight",
  "nine",
  "ten",
  "eleven",
  "twelve",
  "thirteen",
  "fourteen",
  "fifteen",
  "sixteen",
  "seventeen",
  "eighteen",
  "nineteen"
];
const TENS = [
  "",
  "",
  "twenty",
  "thirty",
  "forty",
  "fifty",
  "sixty",
  "seventy",
  "eighty",
  "ninety"
];

/** Spelled-out counts read as prose beside a date; digits read as data. */
function spell(value: number): string {
  if (value < 0 || !Number.isInteger(value)) {
    return String(value);
  }
  if (value < 20) {
    return ONES[value];
  }
  if (value < 100) {
    const tens = TENS[Math.floor(value / 10)];
    const unit = value % 10;
    return unit === 0 ? tens : `${tens}-${ONES[unit]}`;
  }
  return String(value);
}

function parse(value: string): Date | null {
  if (!isSupportedPublicDate(value)) {
    return null;
  }
  return new Date(`${value}T00:00:00Z`);
}

function wholeDays(from: Date, to: Date): number {
  return Math.round((to.getTime() - from.getTime()) / DAY_MS);
}

/**
 * Whole calendar months between two dates, independent of direction.
 *
 * Computed on the ordered pair: counting from the later date backwards
 * gives a different remainder than counting forwards, which would describe
 * one gap as "four months earlier" from one end and "five months later"
 * from the other. The same two dates are the same distance apart.
 */
function wholeMonths(a: Date, b: Date): number {
  const [from, to] = a.getTime() <= b.getTime() ? [a, b] : [b, a];
  const years = to.getUTCFullYear() - from.getUTCFullYear();
  let months = years * 12 + (to.getUTCMonth() - from.getUTCMonth());
  if (to.getUTCDate() < from.getUTCDate()) {
    months -= 1;
  }
  return months;
}

/**
 * Months rounded to the nearest whole month rather than truncated. Four
 * months and nineteen days is closer to five months than to four, and
 * "about" is a claim of proximity, not of a floor.
 */
function nearestMonths(a: Date, b: Date): number {
  const [from, to] = a.getTime() <= b.getTime() ? [a, b] : [b, a];
  const months = wholeMonths(from, to);
  // Clamped to the destination month's last day. setUTCMonth rolls a
  // nonexistent date forward — January 31 plus one month becomes March 3,
  // not February 28 — which moves the very boundary being measured against.
  const anchor = (count: number) => {
    const year = from.getUTCFullYear();
    const month = from.getUTCMonth() + count;
    const firstOfTarget = new Date(Date.UTC(year, month, 1));
    const lastDay = new Date(
      Date.UTC(firstOfTarget.getUTCFullYear(), firstOfTarget.getUTCMonth() + 1, 0)
    ).getUTCDate();
    return Date.UTC(
      firstOfTarget.getUTCFullYear(),
      firstOfTarget.getUTCMonth(),
      Math.min(from.getUTCDate(), lastDay)
    );
  };
  // Compared against the real month boundaries either side, because months
  // are not all the same length: fifteen days into March is not the same
  // fraction of a month as fifteen days into February.
  const below = to.getTime() - anchor(months);
  const above = anchor(months + 1) - to.getTime();
  return below > above ? months + 1 : months;
}

export function distanceBand(from: string, to: string): DistanceBand | null {
  const start = parse(from);
  const end = parse(to);
  if (start === null || end === null) {
    return null;
  }
  const days = Math.abs(wholeDays(start, end));
  if (days <= 31) {
    return "days";
  }
  return days < 365 ? "months" : "years";
}

/**
 * Supporting text for a destination, or null when there is nothing to
 * describe. Callers always render the exact date regardless.
 */
export function describeDistance(from: string, to: string): string | null {
  const start = parse(from);
  const end = parse(to);
  if (start === null || end === null) {
    return null;
  }
  const signedDays = wholeDays(start, end);
  if (signedDays === 0) {
    return null;
  }
  const direction = signedDays > 0 ? "later" : "earlier";
  const days = Math.abs(signedDays);

  if (days <= 31) {
    return `${spell(days)} ${days === 1 ? "day" : "days"} ${direction}`;
  }

  if (days < 365) {
    const months = nearestMonths(start, end);
    // Hedged because a month is not a fixed length: 32 days is "about one
    // month", not "one month".
    const rounded = Math.max(1, months);
    return `about ${spell(rounded)} ${
      rounded === 1 ? "month" : "months"
    } ${direction}`;
  }

  // Rounded, not truncated: the band is chosen in days, so a 365-day gap
  // spanning eleven whole calendar months must still be a year rather than
  // "zero years and eleven months".
  const months = nearestMonths(start, end);
  const years = Math.floor(months / 12);
  const remainderMonths = months % 12;
  const yearText = `${spell(years)} ${years === 1 ? "year" : "years"}`;
  // Months clarify a short year-scale gap and only add noise beyond that.
  if (years <= 2 && remainderMonths > 0) {
    return `${yearText} and ${spell(remainderMonths)} ${
      remainderMonths === 1 ? "month" : "months"
    } ${direction}`;
  }
  return `${yearText} ${direction}`;
}
