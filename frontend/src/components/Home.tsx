import "../styles/home.css";
import { useCallback, useState } from "react";
import { Meter, Panel, RarityBadge, ShinyBadge, Sprite } from "./Primitives";
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
import { axisLabel, barHeight, localDate } from "../lib/trend";
import type { AccountLimitsView, DayPoint, LimitWindowView, StateView } from "../types";

function CompanionCard({ state }: { state: StateView }) {
  const { companion } = state;
  const active = companion.active;
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
            <Meter value={egg.progress} label="Incubation progress" />
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

  const stageLabel = active.is_final
    ? "Final form"
    : `Stage ${active.stage_index + 1} of ${active.total_forms}`;
  const goalLabel = active.is_final ? "to graduation" : "to next evolution";

  return (
    <Panel className={`companion companion--${companion.display_state}`}>
      <div className="companion__body">
        <div className="companion__art">
          <Sprite
            speciesId={active.species_id}
            shiny={active.is_shiny}
            form={active.unown_form}
            size={120}
            alt={active.name}
          />
        </div>
        <div className="companion__info">
          <div className="companion__nameRow">
            <h2 className="companion__name">{active.name}</h2>
            {active.level !== null && <span className="home__level">Lv. {active.level}</span>}
            <RarityBadge rarity={active.rarity} />
            {active.is_shiny && <ShinyBadge />}
            {active.nature_label && <span className="nature">{active.nature_label}</span>}
          </div>
          <p className="companion__stage">
            {stageLabel}
            {active.growth_multiplier ? (
              <span className="home__boost">{active.growth_multiplier}× growth</span>
            ) : null}
          </p>
          <Meter
            value={active.progress}
            tone={active.is_final ? "gold" : "accent"}
            label="Growth progress"
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
              <div
                className={`chain__slot ${slot.is_current ? "chain__slot--current" : ""} ${
                  slot.mystery ? "chain__slot--mystery" : ""
                }`}
              >
                {slot.species_id ? (
                  <Sprite
                    speciesId={slot.species_id}
                    shiny={active.is_shiny}
                    form={active.unown_form}
                    animated={false}
                    size={52}
                    alt={slot.name ?? ""}
                  />
                ) : (
                  <span className="chain__unknown" aria-hidden="true">?</span>
                )}
                <span className="chain__name">{slot.name ?? "???"}</span>
              </div>
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

function MonthTrend({ days, todayKey }: { days: DayPoint[]; todayKey: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const peak = Math.max(0, ...days.map((d) => d.total_tokens));
  if (days.length === 0 || peak <= 0) return null;

  const foundToday = days.findIndex((d) => d.date === todayKey);
  const todayIndex = foundToday >= 0 ? foundToday : days.length - 1;
  const todayDay = localDate(days[todayIndex].date).getDate();
  // The month can shrink under a held hover (at midnight on the 1st).
  const shown = days[hover !== null && hover < days.length ? hover : todayIndex];

  return (
    <div className="trend" onMouseLeave={() => setHover(null)}>
      <div className="trend__caption">
        <span className="trend__title">Daily this month</span>
        <span className="trend__readout">
          {dayStamp(shown.date)} {tokens(shown.total_tokens)}{" "}
          {costText(shown.cost, shown.cost_coverage)}
        </span>
        <span className="trend__peak">
          Peak <strong>{tokens(peak)}</strong>
        </span>
      </div>
      <div className="trend__grid" style={{ gridTemplateColumns: `repeat(${days.length}, 1fr)` }}>
        {days.map((d, i) => {
          const date = localDate(d.date);
          const weekday = date.getDay();
          const label = axisLabel(date.getDate(), todayDay);
          const isToday = i === todayIndex;
          return (
            <div
              key={d.date}
              className="trend__col"
              onMouseEnter={() => setHover(i)}
              role="img"
              aria-label={`${dayStamp(d.date)}, ${exact(d.total_tokens)} tokens`}
            >
              <span className="trend__track">
                <span
                  className={`trend__bar ${isToday ? "trend__bar--today" : ""} ${
                    d.total_tokens <= 0 ? "trend__bar--zero" : ""
                  }`}
                  style={{ height: `${barHeight(d.total_tokens, peak)}px` }}
                />
              </span>
              <span
                className={`trend__weekend ${weekday === 0 || weekday === 6 ? "is-weekend" : ""}`}
              />
              <span className={`trend__axis ${isToday ? "trend__axis--today" : ""}`}>
                {label ?? ""}
              </span>
            </div>
          );
        })}
      </div>
    </div>
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
        <span className="headline__exact">{exact(provider.total_tokens)}</span>
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
          <button
            type="button"
            className="home__recapBtn"
            onClick={onRecap}
            title="Usage recap"
            aria-label="Usage recap"
          >
            <RecapIcon />
          </button>
        </div>
        <div className="rangeRow__item">
          <span className="rangeRow__label">Burn rate</span>
          <span className="rangeRow__value">{tokens(usage.burn_per_minute)}/min</span>
          <span className={`rangeRow__cost burn burn--${usage.burn_tier}`}>
            {burnLabel(usage.burn_tier)}
          </span>
        </div>
      </div>

      <MonthTrend days={usage.month_daily} todayKey={usage.today_date} />

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

export function Home({ state }: { state: StateView }) {
  const [view, setView] = useState<"home" | "recap">("home");
  const back = useCallback(() => setView("home"), []);

  if (view === "recap") return <Recap state={state} onBack={back} />;

  return (
    <div className="stack">
      <CompanionCard state={state} />
      <TokenStats state={state} onRecap={() => setView("recap")} />
      <Limits state={state} />
    </div>
  );
}
