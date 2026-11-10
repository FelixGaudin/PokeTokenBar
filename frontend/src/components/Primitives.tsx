import { useEffect, useState } from "react";
import { spriteUrl } from "../lib/api";
import { tokens } from "../lib/format";
import { slugLabel } from "../lib/dex";
import type { Rarity } from "../types";

/** Wait before retrying a failed sprite: 2, 4, 8, 16 s, then every 30 s. */
function retryDelay(attempt: number): number {
  return Math.min(2000 * 2 ** attempt, 30_000);
}

export function Sprite({
  speciesId,
  shiny = false,
  animated = true,
  size = 96,
  alt = "",
  className = "",
  form = null,
  back = false,
}: {
  speciesId: number;
  shiny?: boolean;
  animated?: boolean;
  size?: number;
  alt?: string;
  className?: string;
  /** The individual's form; species without it show their default. */
  form?: string | null;
  /** Seen from behind. */
  back?: boolean;
}) {
  const [failed, setFailed] = useState(false);
  const [fellBack, setFellBack] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const key = `${speciesId}-${shiny}-${animated}-${form ?? ""}-${back}`;
  const [seenKey, setSeenKey] = useState(key);
  if (seenKey !== key) {
    // A different image: forget the previous one's failures.
    setSeenKey(key);
    setFailed(false);
    setFellBack(false);
    setAttempt(0);
  }

  const preferred = spriteUrl(speciesId, { animated, shiny, form, back });
  const degraded = failed || fellBack;
  useEffect(() => {
    if (!degraded) return;
    // A failed download is usually transient: keep the fallback on screen, retry the
    // preferred image in the background and swap it in once it has loaded.
    let probe: HTMLImageElement | null = null;
    const timer = window.setTimeout(() => {
      probe = new Image();
      probe.onload = () => {
        setFailed(false);
        setFellBack(false);
        setAttempt(0);
      };
      probe.onerror = () => setAttempt((a) => a + 1);
      probe.src = preferred;
    }, retryDelay(attempt));
    return () => {
      window.clearTimeout(timer);
      if (probe) probe.onload = probe.onerror = null;
    };
  }, [degraded, attempt, preferred]);

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
      src={spriteUrl(speciesId, { animated: animated && !fellBack, shiny, form, back })}
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

const DEX_LINK_HINT = "View entry in collection";

/**
 * Makes a sprite open its Pokédex entry. Without a target (egg, uncaught species, `?`
 * node) it renders a plain element, so only real links look clickable.
 */
export function DexLink({
  speciesId,
  onOpen,
  name,
  className = "",
  children,
}: {
  /** Null unless the species is in the Pokédex. */
  speciesId: number | null;
  onOpen?: (speciesId: number) => void;
  name?: string | null;
  className?: string;
  children: React.ReactNode;
}) {
  if (speciesId === null || !onOpen) return <div className={className}>{children}</div>;
  return (
    <button
      type="button"
      className={`dexLink ${className}`}
      title={DEX_LINK_HINT}
      aria-label={name ? `${name}, ${DEX_LINK_HINT}` : DEX_LINK_HINT}
      onClick={() => onOpen(speciesId)}
    >
      {children}
    </button>
  );
}

/** A species' types as game-colored badges. */
export function TypeBadges({ types, className = "" }: { types: string[]; className?: string }) {
  return (
    <span className={`typeBadges ${className}`}>
      {types.map((t) => (
        <span key={t} className={`badge badge--type badge--type-${t.toLowerCase()}`}>
          {slugLabel(t)}
        </span>
      ))}
    </span>
  );
}
