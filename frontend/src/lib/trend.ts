import type { ActivityViewKind, DayPoint, TrendWindow } from "../types";

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

export const WINDOW_ORDER: TrendWindow[] = ["7d", "14d", "21d", "month"];

export const WINDOW_TITLES: Record<TrendWindow, string> = {
  "7d": "Last 7 days",
  "14d": "Last 14 days",
  "21d": "Last 21 days",
  month: "This month",
};

const WINDOW_DAYS: Record<Exclude<TrendWindow, "month">, number> = { "7d": 7, "14d": 14, "21d": 21 };

/**
 * The selected window's columns: the calendar month (future days hold their place), or
 * the last N days of the server's dense 21-day series, none of them in the future.
 */
export function trendColumns(
  window: TrendWindow,
  monthDaily: DayPoint[],
  rollingDaily: DayPoint[],
  todayKey: string,
): TrendColumn[] {
  if (window === "month") return monthColumns(monthDaily, todayKey);
  return rollingDaily.slice(-WINDOW_DAYS[window]).map((point) => ({ point, isFuture: false }));
}

/** Whole calendar days from one "YYYY-MM-DD" to another, positive when `to` is later. */
export function dayDifference(fromKey: string, toKey: string): number {
  const utc = (key: string) => {
    const [y, m, d] = key.split("-").map(Number);
    return Date.UTC(y, (m ?? 1) - 1, d ?? 1);
  };
  return Math.round((utc(toKey) - utc(fromKey)) / 86_400_000);
}

/**
 * Axis label for a column. Today is always labelled. The month window keeps day 1 and
 * the weekly marks; rolling windows label the 1st of a month and the weekly marks back
 * from today (the midpoint in 7 days), dropping a mark that would crowd a nearby 1st.
 */
export function axisLabelFor(
  window: TrendWindow,
  dateKey: string,
  todayKey: string,
  minSep = 3,
): string | null {
  const date = localDate(dateKey);
  const dom = date.getDate();
  if (dateKey === todayKey) return String(dom);
  if (window === "month") return axisLabel(dom, localDate(todayKey).getDate());

  const diff = dayDifference(dateKey, todayKey);
  if (diff <= 0) return null;
  if (window === "7d") {
    if (dom === 1) {
      return diff >= minSep && (diff === 3 || Math.abs(diff - 3) >= minSep) ? "1" : null;
    }
    return diff === 3 ? String(dom) : null;
  }
  if (dom === 1) return diff >= minSep ? "1" : null;
  if (diff % 7 !== 0 || diff < minSep) return null;
  // This month's 1st, in the window and clear of today, is the better landmark.
  if (diff - (dom - 1) >= minSep && dom - 1 < minSep) return null;
  // So is next month's 1st, when the mark sits on the last days of its month.
  const daysInMonth = new Date(date.getFullYear(), date.getMonth() + 1, 0).getDate();
  const toNextFirst = daysInMonth - dom + 1;
  if (diff - toNextFirst >= minSep && toNextFirst < minSep) return null;
  return String(dom);
}

export const ACTIVITY_VIEWS: ActivityViewKind[] = ["monthlyBars", "recentHeatmap"];

/** The next view in the cycle, wrapping around. */
export function nextView(view: ActivityViewKind): ActivityViewKind {
  return ACTIVITY_VIEWS[(ACTIVITY_VIEWS.indexOf(view) + 1) % ACTIVITY_VIEWS.length];
}
