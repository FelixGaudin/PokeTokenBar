import { useState } from "react";
import { itemSpriteUrl } from "../lib/api";
import { exact, tokens } from "../lib/format";
import { Panel, Price } from "./Primitives";
import type { ActiveView, Rarity, ShopEggView, StateView } from "../types";

function ItemIcon({ spriteName, emoji }: { spriteName: string | null; emoji: string }) {
  if (!spriteName) {
    return (
      <span className="itemIcon" role="img" aria-hidden="true">
        {emoji}
      </span>
    );
  }
  return (
    <span className="itemIcon">
      <img
        className="sprite"
        src={itemSpriteUrl(spriteName)}
        alt=""
        width={32}
        height={32}
        draggable={false}
        // PokéAPI has no art for every item; the emoji is the fallback.
        onError={(e) => {
          const el = e.currentTarget;
          el.replaceWith(document.createTextNode(emoji));
        }}
      />
    </span>
  );
}

export function Shop({
  state,
  busy,
  onBuyItem,
  onBuyEgg,
}: {
  state: StateView;
  busy: boolean;
  onBuyItem: (kind: string) => void;
  onBuyEgg: (tier: Rarity | null) => void;
}) {
  const { shop, wallet, companion } = state;
  const hasActive = companion.active !== null;

  return (
    <div className="stack">
      <Panel title="Wallet">
        <div className="wallet">
          <div className="wallet__main">
            <span className="wallet__value">{tokens(wallet.available_tokens)}</span>
            <span className="wallet__exact">{exact(wallet.available_tokens)} spendable</span>
          </div>
          <div className="wallet__side">
            <span>
              Lifetime earned <strong>{tokens(wallet.used_since_install)}</strong>
            </span>
            <span>
              Spent <strong>{tokens(wallet.spent_tokens)}</strong>
            </span>
          </div>
        </div>
        <p className="note">
          Every token you have ever spent on AI is currency here. Buying only draws down
          the wallet — it never touches your growth meter or your usage stats.
        </p>
      </Panel>

      <Panel title="Items">
        <div className="cards">
          {shop.items.map((item) => (
            <article key={item.kind} className="card">
              <div className="card__head">
                <ItemIcon spriteName={item.sprite_name} emoji={item.emoji} />
                <div>
                  <h3 className="card__title">{item.label}</h3>
                  {item.passive && <span className="tag">Held item</span>}
                </div>
              </div>
              <p className="card__desc">{item.description}</p>
              <div className="card__foot">
                <Price value={item.price} affordable={item.affordable} />
                <button
                  className="btn btn--primary"
                  disabled={busy || item.owned || !item.affordable}
                  onClick={() => onBuyItem(item.kind)}
                >
                  {item.owned ? "Owned" : item.affordable ? "Buy" : "Not enough"}
                </button>
              </div>
            </article>
          ))}
        </div>
      </Panel>

      <Panel title="Eggs">
        <p className="note note--lead">
          {hasActive
            ? "A shop egg sends off the Pokémon you are raising right now and starts a new egg."
            : "Eggs unlock once your current egg hatches — a shop egg always sends off the Pokémon you are raising."}
        </p>
        <div className="cards">
          {shop.eggs.map((egg) => (
            <EggCard
              key={egg.tier ?? "any"}
              egg={egg}
              active={companion.active}
              busy={busy}
              onBuy={() => onBuyEgg(egg.tier)}
            />
          ))}
        </div>
      </Panel>
    </div>
  );
}

type EggStep = "idle" | "confirm" | "precious";

function EggCard({
  egg,
  active,
  busy,
  onBuy,
}: {
  egg: ShopEggView;
  active: ActiveView | null;
  busy: boolean;
  onBuy: () => void;
}) {
  const [step, setStep] = useState<EggStep>("idle");
  const canBuy = egg.buyable && egg.affordable;
  // Whatever made the egg unbuyable since the confirm opened also closes it.
  const shown: EggStep = canBuy && active ? step : "idle";
  const legendary = active?.rarity === "legendary";

  function commit() {
    setStep("idle");
    onBuy();
  }

  return (
    <article className="card">
      <div className="card__head">
        <span className="itemIcon" role="img" aria-hidden="true">
          🥚
        </span>
        <div>
          <h3 className="card__title">{egg.label}</h3>
          {egg.tier && <span className={`badge badge--${egg.tier}`}>{egg.tier}+</span>}
        </div>
      </div>
      <div className="card__desc">
        <p className="eggCard__desc">{egg.description}</p>
        {active && (
          <p className="eggCard__release">
            A released Pokémon stays in your Pokédex and can hatch again at the same odds —
            only the growth progress is lost.
          </p>
        )}
      </div>

      {shown === "idle" && (
        <>
          <div className="card__foot">
            <Price value={egg.price} affordable={egg.affordable} />
            <button className="btn" disabled={busy || !canBuy} onClick={() => setStep("confirm")}>
              {!egg.buyable || egg.affordable ? "Buy" : "Not enough"}
            </button>
          </div>
          {!egg.buyable && egg.locked_reason && (
            <p className="eggCard__locked">{egg.locked_reason}</p>
          )}
        </>
      )}

      {shown === "confirm" && active && (
        <div className="eggCard__confirm">
          <p>
            Send off {active.name} for the {egg.label}?
          </p>
          <div className="eggCard__actions">
            <button className="btn" onClick={() => setStep("idle")}>
              Cancel
            </button>
            <button
              className="btn btn--primary"
              disabled={busy}
              onClick={() => (active.is_high_value ? setStep("precious") : commit())}
            >
              Send off
            </button>
          </div>
        </div>
      )}

      {shown === "precious" && active && (
        <div className="eggCard__confirm eggCard__confirm--precious" role="alert">
          <p>
            {legendary
              ? "⚠️ This is a Legendary Pokémon! Really send it off?"
              : "⚠️ This one is shiny! Really send it off?"}
          </p>
          <div className="eggCard__actions">
            <button className="btn" onClick={() => setStep("idle")}>
              Cancel
            </button>
            <button className="btn btn--danger" disabled={busy} onClick={commit}>
              {active.is_shiny && !legendary ? "Send shiny off" : "Send off"}
            </button>
          </div>
        </div>
      )}
    </article>
  );
}
