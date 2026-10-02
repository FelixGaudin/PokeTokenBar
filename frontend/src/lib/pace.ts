import { roundHalfAway, until } from "./format";

export type LimitDisplay = "used" | "remaining";

export type PaceTier = "wayUnder" | "under" | "onPace" | "slightlyOver" | "over" | "wayOver";

export type AbsoluteTone = "ok" | "warn" | "crit";

/** The tier is held back for the first 10% of a window. */
export const MIN_PACE = 0.1;
export const WARN_THRESHOLD = 80;
const RESET_CLOCK_WINDOW_MS = 6 * 3600 * 1000;
const FIVE_HOURS = 5 * 3600;

export function displayPercent(utilization: number, mode: LimitDisplay): number {
  return mode === "remaining" ? Math.max(0, 100 - utilization) : utilization;
}

/** Fill (or marker) fraction 0..1 for a percentage in the current display mode. */
export function displayFraction(utilization: number, mode: LimitDisplay): number {
  return Math.max(0, Math.min(100, displayPercent(utilization, mode))) / 100;
}

/**
 * How far through the window an even burn would be now. Out-of-range values
 * are hidden rather than clamped: an idle 5h window can carry a stale reset.
 */
export function paceFraction(
  resetsAt: string | null,
  spanSeconds: number | null,
  now = Date.now(),
): number | null {
  if (!resetsAt || !spanSeconds || spanSeconds <= 0) return null;
  const reset = new Date(resetsAt).getTime();
  if (Number.isNaN(reset)) return null;
  const fraction = (spanSeconds - (reset - now) / 1000) / spanSeconds;
  if (!Number.isFinite(fraction) || fraction < 0 || fraction > 1) return null;
  return fraction;
}

export function roundedDelta(utilization: number, pace: number): number {
  return roundHalfAway(utilization - pace * 100);
}

export function paceTier(utilization: number, pace: number | null, crit: number): PaceTier | null {
  if (pace === null || pace < MIN_PACE) return null;
  if (utilization >= crit) return "wayOver";
  const d = roundedDelta(utilization, pace);
  if (d < -25) return "wayUnder";
  if (d < -10) return "under";
  if (d < 5) return "onPace";
  if (d < 15) return "slightlyOver";
  if (d < 30) return "over";
  return "wayOver";
}

export const TIER_LABELS: Record<PaceTier, string> = {
  wayUnder: "Well under pace",
  under: "Under pace",
  onPace: "On pace",
  slightlyOver: "Slightly fast",
  over: "Fast",
  wayOver: "Very fast",
};

/** Speaks of the used amount without a sign, so remaining mode reads the same. */
export function deltaText(points: number): string | null {
  if (points > 0) return `${points} pts more used`;
  if (points < 0) return `${Math.abs(points)} pts less used`;
  return null;
}

export function absoluteTone(utilization: number, crit: number): AbsoluteTone {
  if (utilization >= crit) return "crit";
  if (utilization >= WARN_THRESHOLD) return "warn";
  return "ok";
}

function clock(date: Date, now: Date): string {
  const time = new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(date);
  const nearby =
    date.toDateString() === now.toDateString() ||
    date.getTime() - now.getTime() <= RESET_CLOCK_WINDOW_MS;
  if (nearby) return time;
  const weekday = date.toLocaleDateString("en-US", { weekday: "long" });
  return `${weekday} ${date.getDate()}, ${time}`;
}

/** "in 2h 3m (14:30)", or "in 2d 9h (Friday 26, 09:00)" further out. */
export function resetLabel(resetsAt: string | null, now = new Date()): string | null {
  const relative = until(resetsAt);
  if (!relative || !resetsAt) return null;
  return `${relative.replace(/^resets /, "")} (${clock(new Date(resetsAt), now)})`;
}

/** The API reports 0% with no reset until the account's next message. */
export function hasNotStarted(w: {
  utilization: number;
  resets_at: string | null;
  kind: string;
  span_seconds: number | null;
}): boolean {
  const fiveHour = w.kind === "session" || w.span_seconds === FIVE_HOURS;
  return fiveHour && w.utilization === 0 && w.resets_at === null;
}
