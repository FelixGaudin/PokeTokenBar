import { Meter, Panel, RarityBadge, ShinyBadge, Sparkline, Sprite } from "./Primitives";
import { burnLabel, exact, percent, statusLine, tokens, until, usd } from "../lib/format";
import type { StateView } from "../types";

function limitTone(utilization: number): "accent" | "warn" | "crit" {
  if (utilization >= 95) return "crit";
  if (utilization >= 80) return "warn";
  return "accent";
}

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
            <p className="companion__hint">
              <strong>{tokens(egg.tokens_to_hatch)}</strong> to hatch
              <span className="dim"> · {percent(egg.progress * 100)} warm</span>
            </p>
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
            size={120}
            alt={active.name}
          />
        </div>
        <div className="companion__info">
          <div className="companion__nameRow">
            <h2 className="companion__name">{active.name}</h2>
            <RarityBadge rarity={active.rarity} />
            {active.is_shiny && <ShinyBadge />}
            {active.nature_label && <span className="nature">{active.nature_label}</span>}
          </div>
          <p className="companion__stage">{stageLabel}</p>
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

function TokenStats({ state }: { state: StateView }) {
  const { usage } = state;
  const provider = usage.today;
  const models = Object.entries(provider.by_model).sort((a, b) => b[1] - a[1]);

  return (
    <Panel title="Today's tokens" aside={<span className="dim">{usage.today_date}</span>}>
      <div className="headline">
        <span className="headline__value">{tokens(provider.total_tokens)}</span>
        <span className="headline__exact">{exact(provider.total_tokens)}</span>
        <span className="headline__cost">{usd(provider.cost)}</span>
      </div>

      <div className="rangeRow">
        <div className="rangeRow__item">
          <span className="rangeRow__label">This week</span>
          <span className="rangeRow__value">{tokens(usage.week.total_tokens)}</span>
          <span className="rangeRow__cost">{usd(usage.week.cost)}</span>
        </div>
        <div className="rangeRow__item">
          <span className="rangeRow__label">This month</span>
          <span className="rangeRow__value">{tokens(usage.month.total_tokens)}</span>
          <span className="rangeRow__cost">{usd(usage.month.cost)}</span>
        </div>
        <div className="rangeRow__item">
          <span className="rangeRow__label">Burn rate</span>
          <span className="rangeRow__value">{tokens(usage.burn_per_minute)}/min</span>
          <span className={`rangeRow__cost burn burn--${usage.burn_tier}`}>
            {burnLabel(usage.burn_tier)}
          </span>
        </div>
      </div>

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

      {usage.daily_history.length > 1 && <Sparkline points={usage.daily_history} />}
    </Panel>
  );
}

function Limits({ state }: { state: StateView }) {
  const { limits, usage } = state;

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

  if (!limits.available) {
    return (
      <Panel title="Limits (official)">
        <p className="empty">
          {limits.error ?? "Waiting for the first fetch…"}
          {limits.auth_expired && (
            <>
              {" "}
              Run <code>claude /login</code> on the host, then refresh.
            </>
          )}
        </p>
        <LocalBlock state={state} />
      </Panel>
    );
  }

  return (
    <Panel
      title="Limits (official)"
      aside={limits.plan ? <span className="plan">{limits.plan}</span> : undefined}
    >
      {limits.stale && limits.fetched_at && (
        <p className="staleNote">
          Showing the last successful reading from{" "}
          {new Date(limits.fetched_at).toLocaleTimeString()} — the latest refresh failed
          {limits.error ? `: ${limits.error}` : ""}.
        </p>
      )}
      <div className="limits">
        {limits.windows.map((w) => {
          const tone = limitTone(w.utilization);
          const reset = until(w.resets_at);
          return (
            <div key={w.key} className="limits__row">
              <div className="limits__head">
                <span className="limits__name">{w.name}</span>
                <span className={`limits__pct limits__pct--${tone}`}>
                  {percent(w.utilization)}
                </span>
              </div>
              <Meter value={w.utilization / 100} tone={tone} label={w.name} />
              {reset && <span className="limits__reset">{reset}</span>}
            </div>
          );
        })}
        {limits.windows.length === 0 && (
          <p className="empty">No limit windows reported for this account.</p>
        )}
      </div>
      {usage.block && (
        <p className="limits__local dim">
          Local 5-hour block: {tokens(usage.block.total_tokens)} tok · {usd(usage.block.cost)}
        </p>
      )}
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
        {usd(block.cost)} · {tokens(block.tokens_per_minute)} tok/min · window ends{" "}
        {new Date(block.end).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
      </span>
    </div>
  );
}

export function Home({ state }: { state: StateView }) {
  return (
    <div className="stack">
      <CompanionCard state={state} />
      <TokenStats state={state} />
      <Limits state={state} />
    </div>
  );
}
