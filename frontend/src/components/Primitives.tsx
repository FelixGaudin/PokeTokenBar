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
}: {
  speciesId: number;
  shiny?: boolean;
  animated?: boolean;
  size?: number;
  alt?: string;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  const [fellBack, setFellBack] = useState(false);

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
      src={spriteUrl(speciesId, { animated: animated && !fellBack, shiny })}
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

export function Meter({
  value,
  tone = "accent",
  label,
}: {
  value: number;
  tone?: "accent" | "warn" | "crit" | "gold";
  label?: string;
}) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <div
      className={`meter meter--${tone}`}
      role="progressbar"
      aria-valuenow={Math.round(pct)}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label}
    >
      <span className="meter__fill" style={{ width: `${pct}%` }} />
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

/** A 30-day token sparkline. Pure SVG so there is no chart dependency. */
export function Sparkline({
  points,
  height = 44,
}: {
  points: { date: string; total_tokens: number }[];
  height?: number;
}) {
  if (points.length < 2) return null;
  const max = Math.max(...points.map((p) => p.total_tokens), 1);
  const width = 100;
  const step = width / (points.length - 1);
  const coords = points.map((p, i) => {
    const x = i * step;
    const y = height - (p.total_tokens / max) * (height - 4) - 2;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  });
  const line = `M ${coords.join(" L ")}`;
  const area = `${line} L ${width},${height} L 0,${height} Z`;

  return (
    <div className="sparkline">
      <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden="true">
        <path className="sparkline__area" d={area} />
        <path className="sparkline__line" d={line} vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="sparkline__scale">
        <span>{points[0].date.slice(5)}</span>
        <span>peak {tokens(max)}</span>
        <span>{points[points.length - 1].date.slice(5)}</span>
      </div>
    </div>
  );
}

export function Price({ value, affordable }: { value: number; affordable: boolean }) {
  return (
    <span className={`price ${affordable ? "" : "price--short"}`}>
      {tokens(value)} <span className="price__unit">tok</span>
    </span>
  );
}
