export const TRACK_HEIGHT = 26;
export const BAR_FLOOR = 1.5;

/** Bar height in px: a baseline for empty days, otherwise proportional to the peak. */
export function barHeight(tokens: number, peak: number): number {
  if (peak <= 0 || tokens <= 0) return BAR_FLOOR;
  return Math.max(BAR_FLOOR, Math.min(1, tokens / peak) * TRACK_HEIGHT);
}

/**
 * Axis label for a day of the month: today always, day 1 and multiples of the
 * interval unless they would crowd today's label.
 */
export function axisLabel(day: number, today: number, interval = 7, minSep = 3): string | null {
  if (day === today) return String(day);
  if ((day === 1 || day % interval === 0) && Math.abs(day - today) >= minSep) return String(day);
  return null;
}

/** "YYYY-MM-DD" as a local date (not UTC midnight). */
export function localDate(key: string): Date {
  const [y, m, d] = key.split("-").map(Number);
  return new Date(y, (m ?? 1) - 1, d ?? 1);
}
