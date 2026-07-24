import { PUBLIC_DATE_MAX, PUBLIC_DATE_MIN } from "@day-perspective/contracts";

export { PUBLIC_DATE_MAX, PUBLIC_DATE_MIN };

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
