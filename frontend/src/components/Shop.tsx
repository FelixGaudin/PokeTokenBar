import { itemSpriteUrl } from "../lib/api";
import { exact, tokens } from "../lib/format";
import { Panel, Price } from "./Primitives";
import type { Rarity, StateView } from "../types";

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
        <p className="note">
          {hasActive
            ? "Buying an egg releases the Pokémon you are raising right now. It disappears rather than graduating, so your Pokédex is unaffected."
            : "Your egg is already incubating. Buying a graded egg replaces it and restarts incubation."}
        </p>
        <div className="cards">
          {shop.eggs.map((egg) => (
            <article key={egg.tier ?? "any"} className="card">
              <div className="card__head">
                <span className="itemIcon" role="img" aria-hidden="true">
                  🥚
                </span>
                <div>
                  <h3 className="card__title">{egg.label}</h3>
                  {egg.tier && <span className={`badge badge--${egg.tier}`}>{egg.tier}+</span>}
                </div>
              </div>
              <p className="card__desc">{egg.description}</p>
              <div className="card__foot">
                <Price value={egg.price} affordable={egg.affordable} />
                <button
                  className="btn"
                  disabled={busy || !egg.affordable}
                  onClick={() => {
                    const warning = hasActive
                      ? `Release ${companion.active?.name} and start a new ${egg.label}? Its progress is lost for good.`
                      : `Replace your current egg with a ${egg.label}? Incubation restarts from zero.`;
                    if (window.confirm(warning)) onBuyEgg(egg.tier);
                  }}
                >
                  {egg.affordable ? "Buy" : "Not enough"}
                </button>
              </div>
            </article>
          ))}
        </div>
      </Panel>
    </div>
  );
}
