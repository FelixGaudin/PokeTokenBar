import type { CostCoverage } from "../types";

/** Compact token counts: 265_800_000 -> "265.8M". */
export function tokens(n: number): string {
  const abs = Math.abs(n);
  if (abs >= 1e9) return `${trim(n / 1e9)}B`;
  if (abs >= 1e6) return `${trim(n / 1e6)}M`;
  if (abs >= 1e3) return `${trim(n / 1e3)}K`;
  return String(Math.round(n));
}

function trim(v: number): string {
  // Two significant decimals below 10, one above — keeps columns narrow.
  const fixed = Math.abs(v) < 10 ? v.toFixed(2) : v.toFixed(1);
  return fixed.replace(/\.0+$/, "").replace(/(\.\d*[1-9])0+$/, "$1");
}

export function exact(n: number): string {
  return n.toLocaleString("en-US");
}

/** Plain dollars, two decimals, no grouping: `$1234.50`. */
export function usd(n: number): string {
  return `$${n.toFixed(2)}`;
}

/** A cost total that knows where it came from. Unavailable only when nothing was priced. */
export function costText(cost: number, coverage: CostCoverage): string {
  const known = coverage.reported || coverage.estimated;
  if (coverage.unknown && !known) return "Unavailable";
  return usd(cost);
}

/** `50%` for whole numbers, `54.6%` otherwise. */
export function percent(n: number): string {
  return Number.isInteger(n) ? `${n}%` : `${n.toFixed(1)}%`;
}

/** Rounded percent for dense readouts such as limit rows. */
export function percentRound(n: number): string {
  return `${Math.round(n)}%`;
}

/** Swift's `.rounded()`: half away from zero (Math.round rounds -2.5 to -2). */
export function roundHalfAway(x: number): number {
  return Math.sign(x) * Math.round(Math.abs(x));
}

/** "4 days, 23 hr" — matches the catch log's age column. */
export function age(iso: string | null): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const seconds = Math.max(0, (Date.now() - then) / 1000);
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return hours > 0 ? `${days}d ${hours}h ago` : `${days}d ago`;
  if (hours > 0) return minutes > 0 ? `${hours}h ${minutes}m ago` : `${hours}h ago`;
  if (minutes > 0) return `${minutes}m ago`;
  return "just now";
}

/** "resets in 2h 14m" for a limit window. */
export function until(iso: string | null): string | null {
  if (!iso) return null;
  const target = new Date(iso).getTime();
  if (Number.isNaN(target)) return null;
  const seconds = (target - Date.now()) / 1000;
  if (seconds <= 0) return "resetting";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const days = Math.floor(hours / 24);
  if (days >= 1) return `resets in ${days}d ${hours % 24}h`;
  if (hours >= 1) return `resets in ${hours}h ${minutes}m`;
  return `resets in ${Math.max(1, minutes)}m`;
}

export function burnLabel(tier: string): string {
  return (
    {
      idle: "Idle",
      normal: "Working",
      fast: "Fast",
      blazing: "Blazing",
    }[tier] ?? tier
  );
}

const STATUS_LINES: Record<string, string> = {
  egg: "Something is stirring inside the egg.",
  idle: "Taking it easy for now.",
  working: "Today's work is piling up.",
  focus: "Deep in the zone right now.",
  tired: "Worn out — the limit is nearly full.",
  sleep: "Fast asleep. No tokens today yet.",
  levelUp: "Something just happened!",
};

export function statusLine(state: string, evolvedTo: string | null, graduated: string | null): string {
  if (graduated) return `${graduated} graduated to the Pokédex!`;
  if (evolvedTo) return `Evolved into ${evolvedTo}!`;
  return STATUS_LINES[state] ?? "";
}

export function titleCase(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}
