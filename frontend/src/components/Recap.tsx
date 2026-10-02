import { useEffect, useState } from "react";
import { Panel, Sprite } from "./Primitives";
import { api } from "../lib/api";
import { exact, percent, tokens } from "../lib/format";
import { localDate } from "../lib/trend";
import type { RecapScope, RecapView, StateView } from "../types";

const SCOPES: { value: RecapScope; label: string }[] = [
  { value: "week", label: "Week" },
  { value: "month", label: "Month" },
  { value: "year", label: "Year" },
];

const SEGMENTS = 8;
const MONTH_LABEL_DAYS = new Set([1, 8, 15, 22, 29]);

function periodLabel(recap: RecapView): string {
  const start = localDate(recap.start);
  if (recap.scope === "year") return String(start.getFullYear());
  if (recap.scope === "month") {
    return start.toLocaleDateString("en-US", { month: "long", year: "numeric" });
  }
  const last = localDate(recap.end);
  last.setDate(last.getDate() - 1);
  return new Intl.DateTimeFormat("en-US", {
    day: "numeric",
    month: "short",
    year: "numeric",
  }).formatRange(start, last);
}

function bucketLabel(scope: RecapScope, key: string): string | null {
  const date = localDate(key);
  if (scope === "week") {
    return date.toLocaleDateString("en-US", { weekday: "short" }).slice(0, 2).toUpperCase();
  }
  if (scope === "month") return MONTH_LABEL_DAYS.has(date.getDate()) ? String(date.getDate()) : null;
  return date.toLocaleDateString("en-US", { month: "short" }).charAt(0).toUpperCase();
}

function bucketName(scope: RecapScope, key: string): string {
  const date = localDate(key);
  if (scope === "year") return date.toLocaleDateString("en-US", { month: "long" });
  return date.toLocaleDateString("en-US", { weekday: "long", month: "short", day: "numeric" });
}

function litSegments(value: number, peak: number): number {
  if (value <= 0) return 0;
  return Math.max(1, Math.round((value / peak) * SEGMENTS));
}

function RecapCard({ recap, state }: { recap: RecapView; state: StateView }) {
  const peak = Math.max(1, ...recap.buckets.map((b) => b.tokens));
  const active = state.companion.active;
  const graduates = recap.graduated.slice(0, 4);

  return (
    <div className="dexCard">
      <div className="dexCard__head">
        <span className="dexCard__lens" aria-hidden="true" />
        <span className="dexCard__dot dexCard__dot--red" aria-hidden="true" />
        <span className="dexCard__dot dexCard__dot--yellow" aria-hidden="true" />
        <span className="dexCard__dot dexCard__dot--green" aria-hidden="true" />
        <span className="dexCard__period">{periodLabel(recap).toUpperCase()}</span>
      </div>

      <div className="dexCard__screen">
        <div className="dexCard__totalRow">
          <span className="dexCard__total">{tokens(recap.total)}</span>
          <span className="dexCard__unit">TOKENS</span>
          {recap.delta !== null && (
            <span
              className={`dexCard__delta ${
                recap.delta >= 0 ? "dexCard__delta--up" : "dexCard__delta--down"
              }`}
            >
              {recap.delta >= 0 ? "▲" : "▼"}
              {percent(Math.abs(recap.delta) * 100)}
            </span>
          )}
        </div>

        <div className={`dexCard__meters dexCard__meters--${recap.scope}`}>
          {recap.buckets.map((b) => {
            const lit = litSegments(b.tokens, peak);
            const label = bucketLabel(recap.scope, b.key);
            return (
              <div
                key={b.key}
                className={`dexMeter ${b.is_current ? "dexMeter--current" : ""} ${
                  b.has_data ? "dexMeter--data" : ""
                }`}
                role="img"
                aria-label={`${bucketName(recap.scope, b.key)}, ${
                  b.has_data ? exact(b.tokens) : "no data"
                }`}
              >
                <div className="dexMeter__stack">
                  {Array.from({ length: SEGMENTS }, (_, i) => (
                    <span
                      key={i}
                      className={`dexMeter__seg ${SEGMENTS - i <= lit ? "is-lit" : ""}`}
                    />
                  ))}
                </div>
                <span className="dexMeter__label" aria-hidden="true">
                  <span>{label ?? ""}</span>
                </span>
              </div>
            );
          })}
        </div>

        <dl className="dexCard__readouts">
          <div>
            <dt>BEST DAY</dt>
            <dd>{recap.best_day ? tokens(recap.best_day_tokens) : "--"}</dd>
          </div>
          <div>
            <dt>BEST STREAK</dt>
            <dd>{recap.best_streak}d</dd>
          </div>
          <div>
            <dt>ACTIVE DAYS</dt>
            <dd>
              {recap.active_days}/{recap.counted_days}
            </dd>
          </div>
          <div>
            <dt>GRADUATED</dt>
            <dd>{recap.graduated_count}</dd>
          </div>
        </dl>
      </div>

      <div className="dexCard__strip">
        {active ? (
          <Sprite
            speciesId={active.species_id}
            shiny={active.is_shiny}
            form={active.unown_form}
            animated={false}
            size={18}
            alt={active.name}
          />
        ) : (
          <span className="dexCard__egg" aria-label="Egg">
            🥚
          </span>
        )}
        <span className="dexCard__divider" aria-hidden="true" />
        {graduates.length > 0 ? (
          <div className="dexCard__grads">
            {graduates.map((g, i) => (
              <span key={`${g.species_id}-${i}`} className="dexCard__grad">
                <span className="dexCard__gradArt">
                  <Sprite
                    speciesId={g.species_id}
                    shiny={g.is_shiny}
                    form={g.unown_form}
                    animated={false}
                    size={18}
                    alt=""
                  />
                  {g.is_shiny && (
                    <span className="dexCard__shiny" aria-label="Shiny">
                      ✨
                    </span>
                  )}
                </span>
                <span className="dexCard__gradName">{g.name}</span>
              </span>
            ))}
          </div>
        ) : (
          <span className="dexCard__none">No graduation in this period.</span>
        )}
      </div>
    </div>
  );
}

export function Recap({ state, onBack }: { state: StateView; onBack: () => void }) {
  const [scope, setScope] = useState<RecapScope>("week");
  const [offset, setOffset] = useState(0);
  const [recap, setRecap] = useState<RecapView | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onBack();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onBack]);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    api
      .recap(scope, offset)
      .then((next) => {
        if (!cancelled) setRecap(next);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [scope, offset]);

  // Keep the previous card while the next period loads, but only for the same scope.
  const shown = recap && recap.scope === scope ? recap : null;

  return (
    <Panel className="recap">
      <div className="recap__top">
        <button type="button" className="btn btn--ghost" onClick={onBack}>
          ‹ Back
        </button>
        <div className="segmented" role="group" aria-label="Recap period">
          {SCOPES.map((s) => (
            <button
              key={s.value}
              type="button"
              className={s.value === scope ? "is-active" : ""}
              aria-pressed={s.value === scope}
              onClick={() => {
                setScope(s.value);
                setOffset(0);
              }}
            >
              {s.label}
            </button>
          ))}
        </div>
      </div>

      <div className="recap__nav">
        <button
          type="button"
          className="recap__navBtn"
          disabled={!shown?.can_go_back || shown.offset !== offset}
          onClick={() => setOffset((o) => o - 1)}
        >
          ◀ Previous
        </button>
        <button
          type="button"
          className="recap__navBtn"
          disabled={offset >= 0}
          onClick={() => setOffset((o) => Math.min(0, o + 1))}
        >
          Next ▶
        </button>
      </div>

      {error && <p className="empty">Could not load the recap: {error}</p>}
      {!error && !shown && <p className="empty">Loading…</p>}
      {shown && (
        <>
          <RecapCard recap={shown} state={state} />
          <div className="recap__captions">
            {shown.best_day && (
              <p>
                Your best day was{" "}
                {localDate(shown.best_day).toLocaleDateString("en-US", {
                  weekday: "long",
                  month: "short",
                  day: "numeric",
                })}{" "}
                — {exact(shown.best_day_tokens)} tokens.
              </p>
            )}
            {shown.is_in_progress && shown.delta !== null && (
              <p>Compared with the same days last {shown.scope}</p>
            )}
          </div>
        </>
      )}
    </Panel>
  );
}
