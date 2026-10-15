import type { DayPoint } from "../types";

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

export interface TrendColumn {
  point: DayPoint;
  /** A day of this month after today: holds its place, draws nothing. */
  isFuture: boolean;
}

/**
 * One column per day of today's calendar month, so bar widths and axis labels stay put
 * all month. Days missing from the series up to today are zero; days from another month
 * (a series still holding last month just after midnight) are left out.
 */
export function monthColumns(series: DayPoint[], todayKey: string): TrendColumn[] {
  const [year, month, today] = todayKey.split("-").map(Number);
  if (!year || !month || !today) return series.map((point) => ({ point, isFuture: false }));
  const prefix = todayKey.slice(0, 7);
  const byDate = new Map(series.map((d) => [d.date, d]));
  const length = new Date(year, month, 0).getDate();
  return Array.from({ length }, (_, i) => {
    const date = `${prefix}-${String(i + 1).padStart(2, "0")}`;
    const point = byDate.get(date) ?? {
      date,
      total_tokens: 0,
      cost: 0,
      cost_coverage: { reported: false, estimated: false, unknown: false },
    };
    return { point, isFuture: i + 1 > today };
  });
}
