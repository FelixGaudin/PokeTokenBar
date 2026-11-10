import "../styles/home.css";
import { useCallback, useEffect, useState } from "react";
import {
  DexLink,
  Meter,
  Panel,
  RarityBadge,
  ShinyBadge,
  Sprite,
  TypeBadges,
} from "./Primitives";
import { api } from "../lib/api";
import type { MeterTone } from "./Primitives";
import { Recap } from "./Recap";
import {
  burnLabel,
  costText,
  exact,
  percent,
  percentRound,
  statusLine,
  tokens,
} from "../lib/format";
import {
  TIER_LABELS,
  absoluteTone,
  deltaText,
  displayFraction,
  displayPercent,
  hasNotStarted,
  paceFraction,
  paceTier,
  resetLabel,
  roundedDelta,
} from "../lib/pace";
import type { LimitDisplay } from "../lib/pace";
import {
  ACTIVITY_VIEWS,
  WINDOW_ORDER,
  WINDOW_TITLES,
  axisLabelFor,
  barHeight,
  localDate,
  nextView,
  trendColumns,
} from "../lib/trend";
import type {
  AccountLimitsView,
  ActivityView,
  ActivityViewKind,
  DayPoint,
  LimitWindowView,
  StateView,
  StreakView,
  TrendWindow,
} from "../types";

/** "X / Y · Z%": used clamps to the total (egg usage can overshoot while a hatch retries) and the percent floors, so 100% means done. */
function progressDetail(used: number, total: number): string {
  const clamped = Math.min(used, total);
  const pct = total > 0 ? Math.floor((clamped * 100) / total) : 0;
  return `${tokens(clamped)} / ${tokens(total)} · ${pct}%`;
}

/** Growth bar that grows on a held hover and shows the exact numbers inside it. */
function GrowthMeter({
  value,
  tone,
  label,
  detail,
}: {
  value: number;
  tone: MeterTone;
  label: string;
  detail: string;
}) {
  return (
    <div className="growthMeter">
      <Meter value={value} tone={tone} label={`${label}: ${detail}`} />
      <span className="growthMeter__detail" aria-hidden="true">
        {detail}
      </span>
    </div>
  );
}

function CompanionCard({
  state,
  onOpenDexEntry,
}: {
  state: StateView;
  onOpenDexEntry: (speciesId: number) => void;
}) {
  const { companion } = state;
  const active = companion.active;
  const speciesId = active?.species_id ?? null;
  // Types come from the PokéAPI details. Same id as the sprite, so a disguised Ditto
  // shows its disguise's types.
  const [types, setTypes] = useState<{ id: number; types: string[] } | null>(null);
  useEffect(() => {
    if (speciesId === null) return;
    let cancelled = false;
    api.pokemon(speciesId).then(
      (data) => !cancelled && setTypes({ id: speciesId, types: data.types }),
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, [speciesId]);
  const line = statusLine(
    companion.display_state,
    companion.just_evolved_to,
    companion.just_graduated,
  );

  if (!active) {
    const egg = companion.egg;
    return (
      <Panel className="companion companion--egg">
        <div className="companion__body">
          <div className="companion__art companion__art--egg" aria-hidden="true">
            🥚
          </div>
          <div className="companion__info">
            <div className="companion__nameRow">
              <h2 className="companion__name">Egg</h2>
              {egg.guaranteed_tier && (
                <span className={`badge badge--${egg.guaranteed_tier}`}>
                  {egg.guaranteed_tier}+ guaranteed
                </span>
              )}
            </div>
            <p className="companion__stage">Incubating</p>
            <GrowthMeter
              value={egg.progress}
              tone="accent"
              label="Incubation progress"
              detail={progressDetail(egg.usage, egg.threshold)}
            />
            {egg.hatch_delayed ? (
              <p className="companion__hint home__delayed">
                ⏳ Hatching is delayed — retrying on the next refresh
              </p>
            ) : (
              <p className="companion__hint">
                <strong>{tokens(egg.tokens_to_hatch)}</strong> to hatch
                <span className="dim"> · {percent(egg.progress * 100)} warm</span>
              </p>
            )}
            {egg.usage === 0 && (
              <p className="companion__hint dim">
                Grows from your local AI coding usage. Your egg hatches after ~
                {tokens(egg.threshold)} tokens.
              </p>
            )}
            <p className="companion__status">{line}</p>
          </div>
        </div>
      </Panel>
    );
  }

  const dexLink = (speciesId: number) =>
    state.collection.pokedex.some((s) => s.species_id === speciesId) ? speciesId : null;
  const stageLabel = active.is_final
    ? "Final form"
    : `Stage ${active.stage_index + 1} of ${active.total_forms}`;
  const goalLabel = active.is_final ? "to graduation" : "to next evolution";

  return (
    <Panel className={`companion companion--${companion.display_state}`}>
      <div className="companion__body">
        <div className="companion__art">
          <DexLink
            speciesId={dexLink(active.species_id)}
            onOpen={onOpenDexEntry}
            name={active.name}
            className="companion__link"
          >
            <Sprite
              speciesId={active.species_id}
              shiny={active.is_shiny}
              form={active.form}
              back={companion.faces_back}
              size={120}
              alt={active.name}
            />
          </DexLink>
        </div>
        <div className="companion__info">
          <div className="companion__nameRow">
            <h2 className="companion__name">{active.name}</h2>
            {active.level !== null && <span className="home__level">Lv. {active.level}</span>}
            <RarityBadge rarity={active.rarity} />
            {active.is_shiny && <ShinyBadge />}
            {active.nature_label && <span className="nature">{active.nature_label}</span>}
          </div>
          {types?.id === active.species_id && types.types.length > 0 && (
            <>
              <TypeBadges types={types.types} className="home__types" />
              {active.shows_alternate_form && (
                <p className="home__formNote">Data uses the default form</p>
              )}
            </>
          )}
          <p className="companion__stage">
            {stageLabel}
            {active.growth_multiplier ? (
              <span className="home__boost">{active.growth_multiplier}× growth</span>
            ) : null}
          </p>
          <GrowthMeter
            value={active.progress}
            tone={active.is_final ? "gold" : "accent"}
            label="Growth progress"
            detail={progressDetail(active.used_at_stage, active.threshold)}
          />
          <p className="companion__hint">
            <strong>{tokens(active.tokens_to_next)}</strong> {goalLabel}
            <span className="dim">
              {" "}
              · {tokens(active.used_at_stage)} / {tokens(active.threshold)}
            </span>
          </p>
          <p className="companion__status">{line}</p>
          {!active.line_loaded && (
            <p className="companion__warn">
              Evolution data is still loading — growth is being banked in the meantime.
            </p>
          )}
        </div>
      </div>

      {active.chain.length > 1 && (
        <div className="chain" aria-label="Evolution line">
          {active.chain.map((slot, index) => (
            <div key={index} className="chain__item">
              {index > 0 && <span className="chain__arrow" aria-hidden="true">→</span>}
              <DexLink
                speciesId={slot.species_id === null ? null : dexLink(slot.species_id)}
                onOpen={onOpenDexEntry}
                name={slot.name}
                className={`chain__slot ${slot.is_current ? "chain__slot--current" : ""} ${
                  slot.mystery ? "chain__slot--mystery" : ""
                }`}
              >
                {slot.species_id ? (
                  <Sprite
                    speciesId={slot.species_id}
                    shiny={active.is_shiny}
                    form={active.form}
                    animated={false}
                    size={52}
                    alt={slot.name ?? ""}
                  />
                ) : (
                  <span className="chain__unknown" aria-hidden="true">?</span>
                )}
                <span className="chain__name">{slot.name ?? "???"}</span>
              </DexLink>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function dayStamp(key: string): string {
  return localDate(key).toLocaleDateString("en-US", {
    weekday: "short",
    month: "numeric",
    day: "numeric",
  });
}

/** One dot per view; the current one stands out. The only hint that the row switches. */
function PageDots({ view, onSwitch }: { view: ActivityViewKind; onSwitch: () => void }) {
  return (
    <button
      type="button"
      className="activity__dots"
      aria-label="Switch view"
      onClick={(e) => {
        e.stopPropagation();
        onSwitch();
      }}
    >
      {ACTIVITY_VIEWS.map((v) => (
        <span key={v} className={`activity__dot ${v === view ? "is-current" : ""}`} />
      ))}
    </button>
  );
}

function DailyTrend({
  monthDaily,
  rollingDaily,
  todayKey,
  period,
  onPeriod,
  hasHistory,
  dots,
}: {
  monthDaily: DayPoint[];
  rollingDaily: DayPoint[];
  todayKey: string;
  period: TrendWindow;
  onPeriod: (period: TrendWindow) => void;
  /** Some day of the month or the last 21 days has usage. */
  hasHistory: boolean;
  dots: React.ReactNode;
}) {
  const [hover, setHover] = useState<string | null>(null);
  const columns = trendColumns(period, monthDaily, rollingDaily, todayKey);
  const peak = Math.max(0, ...columns.map((c) => c.point.total_tokens));
  // An empty window keeps its caption while other days have usage, so it can be changed.
  if (columns.length === 0 || (peak <= 0 && !hasHistory)) return null;

  const todayColumn = columns.find((c) => c.point.date === todayKey) ?? columns[columns.length - 1];
  // The window can change under a held hover (at midnight).
  const shown = (columns.find((c) => c.point.date === hover) ?? todayColumn).point;
  // With a single day of usage the peak would repeat the readout.
  const showsPeak = columns.filter((c) => c.point.total_tokens > 0).length > 1;

  return (
    <div className="trend" onMouseLeave={() => setHover(null)}>
      <div className="trend__caption">
        <select
          className="trend__window"
          aria-label="Daily trend period"
          value={period}
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => onPeriod(e.target.value as TrendWindow)}
        >
          {WINDOW_ORDER.map((w) => (
            <option key={w} value={w}>
              {WINDOW_TITLES[w]}
            </option>
          ))}
        </select>
        <span className="trend__readout">
          {dayStamp(shown.date)} {tokens(shown.total_tokens)}{" "}
          {costText(shown.cost, shown.cost_coverage)}
        </span>
        <span className="trend__end">
          {showsPeak && (
            <span className="trend__peak">
              Peak <strong>{tokens(peak)}</strong>
            </span>
          )}
          {dots}
        </span>
      </div>
      {peak > 0 && (
        <div
          className="trend__grid"
          style={{ gridTemplateColumns: `repeat(${columns.length}, 1fr)` }}
        >
          {columns.map(({ point: d, isFuture }) => {
            const weekday = localDate(d.date).getDay();
            const label = axisLabelFor(period, d.date, todayKey);
            const isToday = d.date === todayColumn.point.date;
            const axisClass = isToday
              ? "trend__axis--today"
              : isFuture
                ? "trend__axis--future"
                : "";
            return (
              <div
                key={d.date}
                className="trend__col"
                onMouseEnter={isFuture ? undefined : () => setHover(d.date)}
                role={isFuture ? undefined : "img"}
                aria-label={
                  isFuture ? undefined : `${dayStamp(d.date)}, ${exact(d.total_tokens)} tokens`
                }
              >
                <span className="trend__track">
                  {!isFuture && (
                    <span
                      className={`trend__bar ${isToday ? "trend__bar--today" : ""} ${
                        d.total_tokens <= 0 ? "trend__bar--zero" : ""
                      }`}
                      style={{ height: `${barHeight(d.total_tokens, peak)}px` }}
                    />
                  )}
                </span>
                <span
                  className={`trend__weekend ${
                    !isFuture && (weekday === 0 || weekday === 6) ? "is-weekend" : ""
                  }`}
                />
                <span className={`trend__axis ${axisClass}`}>{label ?? ""}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

const WEEKDAY_LETTERS = ["M", "T", "W", "T", "F", "S", "S"];

/** 16 Monday-first weeks of daily totals, the ledger's history included. */
function ActivityGrid({
  activity,
  monthDaily,
  todayKey,
  hasHistory,
  dots,
}: {
  activity: ActivityView;
  monthDaily: DayPoint[];
  todayKey: string;
  hasHistory: boolean;
  dots: React.ReactNode;
}) {
  const [hover, setHover] = useState<string | null>(null);
  if (activity.peak <= 0 && !hasHistory) return null;

  // Cells are too small for their own tooltip, so the caption follows the pointer.
  const shownKey = hover ?? todayKey;
  const live = monthDaily.find((d) => d.date === shownKey);
  const cell = activity.weeks.flat().find((c) => c.date === shownKey);
  const readout = live
    ? `${dayStamp(live.date)} ${tokens(live.total_tokens)} ${costText(live.cost, live.cost_coverage)}`
    : cell
      ? `${dayStamp(cell.date)} ${tokens(cell.tokens)}`
      : "";

  return (
    <div className="trend" onMouseLeave={() => setHover(null)}>
      <div className="trend__caption">
        <span className="trend__title">16 weeks</span>
        <span className="trend__readout">{readout}</span>
        <span className="trend__end">
          {activity.used_days > 1 && (
            <span className="trend__peak">
              Peak <strong>{tokens(activity.peak)}</strong>
            </span>
          )}
          {dots}
        </span>
      </div>
      {activity.peak > 0 && (
        <div className="heat" role="img" aria-label="Daily usage over the last 16 weeks">
          {WEEKDAY_LETTERS.map((letter, i) => (
            <span key={`label-${i}`} className="heat__label" aria-hidden="true">
              {letter}
            </span>
          ))}
          {activity.weeks.flat().map((c) => (
            <span
              key={c.date}
              className={`heat__cell ${
                c.in_range ? `heat__cell--l${c.level}` : "heat__cell--out"
              } ${c.date === todayKey ? "heat__cell--today" : ""}`}
              onMouseEnter={c.in_range ? () => setHover(c.date) : undefined}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/** The daily bars or the 16-week grid; a click anywhere on it switches between them. */
function ActivityPanel({ state }: { state: StateView }) {
  const { usage, meta } = state;
  // Optimistic copies of the stored choices, re-synced whenever the server's change.
  const [view, setView] = useState({ synced: meta.activity_view, value: meta.activity_view });
  if (view.synced !== meta.activity_view) {
    setView({ synced: meta.activity_view, value: meta.activity_view });
  }
  const [period, setPeriod] = useState({
    synced: meta.daily_trend_window,
    value: meta.daily_trend_window,
  });
  if (period.synced !== meta.daily_trend_window) {
    setPeriod({ synced: meta.daily_trend_window, value: meta.daily_trend_window });
  }

  const cycle = () => {
    const next = nextView(view.value);
    setView({ ...view, value: next });
    api.setActivityView(next).catch(() => setView((v) => ({ ...v, value: v.synced })));
  };
  const choosePeriod = (next: TrendWindow) => {
    setPeriod({ ...period, value: next });
    api.setTrendWindow(next).catch(() => setPeriod((w) => ({ ...w, value: w.synced })));
  };

  const hasHistory = [...usage.month_daily, ...usage.rolling_daily].some(
    (d) => d.total_tokens > 0,
  );
  const dots = <PageDots view={view.value} onSwitch={cycle} />;

  return (
    <div className="activity" title="Click to switch the view" onClick={cycle}>
      {view.value === "recentHeatmap" ? (
        <ActivityGrid
          activity={usage.activity}
          monthDaily={usage.month_daily}
          todayKey={usage.today_date}
          hasHistory={hasHistory}
          dots={dots}
        />
      ) : (
        <DailyTrend
          monthDaily={usage.month_daily}
          rollingDaily={usage.rolling_daily}
          todayKey={usage.today_date}
          period={period.value}
          onPeriod={choosePeriod}
          hasHistory={hasHistory}
          dots={dots}
        />
      )}
    </div>
  );
}

function FlameIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true">
      <path
        fill="currentColor"
        d="M8.6 1c.3 2.2-.6 3.5-1.6 4.7C6 6.9 5 8 5 9.8a3 3 0 0 0 1.4 2.6c-.2-1.4.5-2.4 1.3-3.2.3 1.3 1.6 1.8 1.8 3.2.1.4 0 .9-.1 1.2A3.6 3.6 0 0 0 12 10c0-1.6-.7-2.8-1.3-3.7.1.9-.2 1.6-.8 2 0-2.9-.8-5.3-1.3-7.3ZM8 15.5c-2.9 0-5-2.1-5-5 0-1.4.5-2.6 1.2-3.6-.1 1.4.2 2.5 1 3.2-.1-2.5 1-4 2-5.3.9-1.2 1.6-2.1 1.4-3.8 2 1.3 4.4 4.3 4.4 8.5 0 3.5-2.1 6-5 6Z"
      />
    </svg>
  );
}

/** Consecutive days with usage, from two. Gray until today's first use extends it. */
function StreakBadge({ streak }: { streak: StreakView }) {
  if (streak.days < 2) return null;
  const label = streak.includes_today
    ? `${streak.days}-day streak`
    : `${streak.days}-day streak — use it today to keep it going`;
  return (
    <span
      className={`home__streak ${streak.includes_today ? "is-lit" : ""}`}
      title={label}
      aria-label={label}
      role="img"
    >
      <FlameIcon />
      <span aria-hidden="true">{streak.days}</span>
    </span>
  );
}

function RecapIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
      <path d="M2 13.5h12" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <rect x="3" y="7" width="2.2" height="5" rx="0.6" fill="currentColor" />
      <rect x="6.9" y="3" width="2.2" height="9" rx="0.6" fill="currentColor" />
      <rect x="10.8" y="5.5" width="2.2" height="6.5" rx="0.6" fill="currentColor" />
    </svg>
  );
}

function TokenStats({ state, onRecap }: { state: StateView; onRecap: () => void }) {
  const { usage } = state;
  const provider = usage.today;
  const models = Object.entries(provider.by_model).sort((a, b) => b[1] - a[1]);

  return (
    <Panel title="Today's tokens" aside={<span className="dim">{usage.today_date}</span>}>
      <div className="headline">
        <span className="headline__value">{tokens(provider.total_tokens)}</span>
        {provider.total_tokens >= 1_000 && (
          <span className="headline__exact">{exact(provider.total_tokens)}</span>
        )}
        <span className="headline__cost">{costText(provider.cost, provider.cost_coverage)}</span>
      </div>

      <div className="rangeRow">
        <div className="rangeRow__item">
          <span className="rangeRow__label">This week</span>
          <span className="rangeRow__value">{tokens(usage.week.total_tokens)}</span>
          <span className="rangeRow__cost">
            {costText(usage.week.cost, usage.week.cost_coverage)}
          </span>
        </div>
        <div className="rangeRow__item home__monthItem">
          <span className="rangeRow__label">This month</span>
          <span className="rangeRow__value">{tokens(usage.month.total_tokens)}</span>
          <span className="rangeRow__cost">
            {costText(usage.month.cost, usage.month.cost_coverage)}
          </span>
          <span className="home__monthActions">
            <StreakBadge streak={usage.streak} />
            <button
              type="button"
              className="home__recapBtn"
              onClick={onRecap}
              title="Usage recap"
              aria-label="Usage recap"
            >
              <RecapIcon />
              <span className="home__recapLabel">Details</span>
            </button>
          </span>
        </div>
        <div className="rangeRow__item">
          <span className="rangeRow__label">Burn rate</span>
          <span className="rangeRow__value">{tokens(usage.burn_per_minute)}/min</span>
          <span className={`rangeRow__cost burn burn--${usage.burn_tier}`}>
            {burnLabel(usage.burn_tier)}
          </span>
        </div>
      </div>

      <ActivityPanel state={state} />

      <div className="breakdown">
        <span className="breakdown__title">Claude Code</span>
        <span className="breakdown__pair">
          in <strong>{tokens(provider.input)}</strong>
        </span>
        <span className="breakdown__pair">
          out <strong>{tokens(provider.output)}</strong>
        </span>
        <span className="breakdown__pair dim">
          cache w <strong>{tokens(provider.cache_write)}</strong>
        </span>
        <span className="breakdown__pair dim">
          cache r <strong>{tokens(provider.cache_read)}</strong>
        </span>
      </div>

      {models.length > 0 && (
        <div className="models">
          {models.map(([model, total]) => (
            <span key={model} className="models__row">
              <code>{model}</code>
              <span>{tokens(total)}</span>
            </span>
          ))}
        </div>
      )}
    </Panel>
  );
}

const ABSOLUTE_METER: Record<ReturnType<typeof absoluteTone>, MeterTone> = {
  ok: "onPace",
  warn: "warn",
  crit: "crit",
};

function LimitRow({
  w,
  mode,
  crit,
}: {
  w: LimitWindowView;
  mode: LimitDisplay;
  crit: number;
}) {
  const u = w.utilization;
  const pace = paceFraction(w.resets_at, w.span_seconds);
  const tier = paceTier(u, pace, crit);
  const absolute = absoluteTone(u, crit);
  const tone: MeterTone = tier ?? ABSOLUTE_METER[absolute];
  const pctClass = tier ?? absolute;

  const shownPct = percentRound(displayPercent(u, mode));
  const pctText = mode === "remaining" ? `${shownPct} left` : shownPct;
  const reset =
    resetLabel(w.resets_at) ?? (hasNotStarted(w) ? "Starts with your next message" : null);

  let tooltip: string | undefined;
  if (pace !== null) {
    const hint = `Pace — an even burn across this window would sit at ${percent(
      displayPercent(pace * 100, mode),
    )} now.`;
    if (tier) {
      const delta = deltaText(roundedDelta(u, pace));
      tooltip = `${TIER_LABELS[tier]}${delta ? ` · ${delta}` : ""}\n${hint}`;
    } else {
      tooltip = hint;
    }
  }

  return (
    <div className="limits__row" title={tooltip}>
      <div className="limits__head home__limitHead">
        <span className="limits__name">{w.name}</span>
        {reset && <span className="home__reset">{reset}</span>}
        <span className={`limits__pct home__pct home__pct--${pctClass}`}>{pctText}</span>
      </div>
      <Meter
        value={displayFraction(u, mode)}
        tone={tone}
        label={w.name}
        marker={pace === null ? null : displayFraction(pace * 100, mode)}
      />
    </div>
  );
}

function AccountLimits({ account, state }: { account: AccountLimitsView; state: StateView }) {
  const { meta, usage } = state;

  if (!account.available) {
    return (
      <>
        <p className="empty">
          {account.error ?? "Waiting for the first fetch…"}
          {account.auth_expired &&
            (account.is_default ? (
              <>
                {" "}
                Run <code>claude /login</code> on the host, then refresh.
              </>
            ) : (
              <>
                {" "}
                Run Claude Code once with <code>CLAUDE_CONFIG_DIR={account.folder ?? "…"}</code>,
                then retry.
              </>
            ))}
        </p>
        <LocalBlock state={state} />
      </>
    );
  }

  return (
    <>
      {account.stale && account.fetched_at && (
        <p className="staleNote">
          Showing the last successful reading from{" "}
          {new Date(account.fetched_at).toLocaleTimeString()} — the latest refresh failed
          {account.error ? `: ${account.error}` : ""}.
        </p>
      )}
      {(account.plan || account.account) && (
        <div className="home__identity">
          {account.plan && <span>Plan {account.plan}</span>}
          {account.account && <span>Account {account.account}</span>}
        </div>
      )}
      <div className="limits">
        {account.windows.map((w) => (
          <LimitRow key={w.key} w={w} mode={meta.limit_display} crit={meta.crit_threshold} />
        ))}
        {account.windows.length === 0 && (
          <p className="empty">No limit windows reported for this account.</p>
        )}
      </div>
      {usage.block && (
        <p className="limits__local dim">
          Local 5-hour block: {tokens(usage.block.total_tokens)} tok ·{" "}
          {costText(usage.block.cost, usage.block.cost_coverage)}
        </p>
      )}
    </>
  );
}

function Limits({ state }: { state: StateView }) {
  const { limits } = state;
  const [selected, setSelected] = useState<string | null>(null);

  if (!limits.enabled) {
    return (
      <Panel title="Limits">
        <p className="empty">
          Official limits are turned off. The rolling 5-hour block below is computed
          entirely from local logs.
        </p>
        <LocalBlock state={state} />
      </Panel>
    );
  }

  const accounts: AccountLimitsView[] =
    limits.accounts.length > 0
      ? limits.accounts
      : [
          {
            id: "default",
            title: "Default",
            is_default: true,
            available: limits.available,
            stale: limits.stale,
            plan: limits.plan,
            account: limits.account,
            windows: limits.windows,
            error: limits.error,
            auth_expired: limits.auth_expired,
            fetched_at: limits.fetched_at,
            folder: null,
          },
        ];
  const current = accounts.find((a) => a.id === selected) ?? accounts[0];

  return (
    <Panel title="Limits (official)">
      {accounts.length > 1 && (
        <div className="tabs home__accountTabs" role="tablist" aria-label="Claude accounts">
          {accounts.map((a) => (
            <button
              key={a.id}
              type="button"
              role="tab"
              aria-selected={a.id === current.id}
              className={`tabs__btn ${a.id === current.id ? "is-active" : ""}`}
              title={a.account ?? undefined}
              onClick={() => setSelected(a.id)}
            >
              {a.title}
            </button>
          ))}
        </div>
      )}
      <AccountLimits account={current} state={state} />
    </Panel>
  );
}

function LocalBlock({ state }: { state: StateView }) {
  const block = state.usage.block;
  if (!block) return <p className="empty">No activity in the last 5 hours.</p>;
  return (
    <div className="limits__row">
      <div className="limits__head">
        <span className="limits__name">Rolling 5-hour block</span>
        <span className="limits__pct">{tokens(block.total_tokens)}</span>
      </div>
      <span className="limits__reset">
        {costText(block.cost, block.cost_coverage)} · {tokens(block.tokens_per_minute)} tok/min ·
        window ends{" "}
        {new Date(block.end).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
      </span>
    </div>
  );
}

export function Home({
  state,
  onOpenDexEntry,
}: {
  state: StateView;
  onOpenDexEntry: (speciesId: number) => void;
}) {
  const [view, setView] = useState<"home" | "recap">("home");
  const back = useCallback(() => setView("home"), []);

  if (view === "recap") return <Recap state={state} onBack={back} />;

  return (
    <div className="stack">
      <CompanionCard state={state} onOpenDexEntry={onOpenDexEntry} />
      <TokenStats state={state} onRecap={() => setView("recap")} />
      <Limits state={state} />
    </div>
  );
}
