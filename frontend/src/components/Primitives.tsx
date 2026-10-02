import { useState } from "react";
import { spriteUrl } from "../lib/api";
import { tokens } from "../lib/format";
import type { Rarity } from "../types";

export function Sprite({
  speciesId,
  shiny = false,
  animated = true,
  size = 96,
  alt = "",
  className = "",
  form = null,
}: {
  speciesId: number;
  shiny?: boolean;
  animated?: boolean;
  size?: number;
  alt?: string;
  className?: string;
  /** Unown letter; ignored for every other species. */
  form?: string | null;
}) {
  const [failed, setFailed] = useState(false);
  const [fellBack, setFellBack] = useState(false);
  const key = `${speciesId}-${shiny}-${animated}-${form ?? ""}`;
  const [seenKey, setSeenKey] = useState(key);
  if (seenKey !== key) {
    // A different image: forget the previous one's failures.
    setSeenKey(key);
    setFailed(false);
    setFellBack(false);
  }

  if (failed) {
    return (
      <span
        className={`sprite sprite--missing ${className}`}
        style={{ width: size, height: size }}
        role="img"
        aria-label={alt || "sprite unavailable"}
      >
        ?
      </span>
    );
  }

  return (
    <img
      className={`sprite ${className}`}
      style={{ width: size, height: size }}
      // Animated Gen-V art only exists up to #649; fall back to the static PNG.
      src={spriteUrl(speciesId, { animated: animated && !fellBack, shiny, form })}
      alt={alt}
      loading="lazy"
      draggable={false}
      onError={() => (fellBack || !animated ? setFailed(true) : setFellBack(true))}
    />
  );
}

export function RarityBadge({ rarity }: { rarity: Rarity }) {
  return <span className={`badge badge--${rarity}`}>{rarity}</span>;
}

export function ShinyBadge() {
  return <span className="badge badge--shiny">✨ Shiny</span>;
}

export type MeterTone =
  | "accent"
  | "warn"
  | "crit"
  | "gold"
  | "wayUnder"
  | "under"
  | "onPace"
  | "slightlyOver"
  | "over"
  | "wayOver";

export function Meter({
  value,
  tone = "accent",
  label,
  marker = null,
}: {
  value: number;
  tone?: MeterTone;
  label?: string;
  /** Optional tick (0..1), e.g. where an even burn would sit now. */
  marker?: number | null;
}) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <div
      className={`meter meter--${tone} ${marker !== null ? "meter--marked" : ""}`}
      role="progressbar"
      aria-valuenow={Math.round(pct)}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label}
    >
      <span className="meter__fill" style={{ width: `${pct}%` }} />
      {marker !== null && (
        <span
          className="meter__marker"
          aria-hidden="true"
          // Never overhang the ends: the tick's own width is subtracted.
          style={{ left: `calc((100% - 2.5px) * ${Math.max(0, Math.min(1, marker))})` }}
        />
      )}
    </div>
  );
}

export function Panel({
  title,
  aside,
  children,
  className = "",
}: {
  title?: string;
  aside?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {(title || aside) && (
        <header className="panel__head">
          {title && <h2 className="panel__title">{title}</h2>}
          {aside && <div className="panel__aside">{aside}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Price({ value, affordable }: { value: number; affordable: boolean }) {
  return (
    <span className={`price ${affordable ? "" : "price--short"}`}>
      {tokens(value)} <span className="price__unit">tok</span>
    </span>
  );
}
