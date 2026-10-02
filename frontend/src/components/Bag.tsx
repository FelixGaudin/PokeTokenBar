import { useState } from "react";
import { itemSpriteUrl } from "../lib/api";
import { tokens } from "../lib/format";
import { Panel } from "./Primitives";
import type { BagItemView, StateView } from "../types";

const CANDY_XP = 100_000_000;

export function Bag({
  state,
  busy,
  onUseCandy,
  onUseMint,
}: {
  state: StateView;
  busy: boolean;
  onUseCandy: (count: number) => void;
  onUseMint: () => void;
}) {
  const { bag, companion } = state;

  if (bag.length === 0) {
    return (
      <Panel title="Bag">
        <p className="empty">
          Your bag is empty. Rare Candy is granted free whenever you fill a rate-limit
          window, or you can buy items in the Shop.
        </p>
      </Panel>
    );
  }

  const unusableReason = companion.active
    ? "Waiting for the evolution line to load"
    : "You need an active Pokémon";

  return (
    <div className="stack">
      <Panel title="Bag">
        <div className="cards">
          {bag.map((item) => {
            const head = (
              <div className="card__head">
                <span className="itemIcon">
                  {item.sprite_name ? (
                    <img
                      className="sprite"
                      src={itemSpriteUrl(item.sprite_name)}
                      alt=""
                      width={32}
                      height={32}
                      draggable={false}
                      onError={(e) =>
                        e.currentTarget.replaceWith(document.createTextNode(item.emoji))
                      }
                    />
                  ) : (
                    item.emoji
                  )}
                </span>
                <div>
                  <h3 className="card__title">{item.label}</h3>
                  <span className="tag">
                    {item.passive ? "Active while held" : `×${item.count}`}
                  </span>
                </div>
              </div>
            );

            if (item.kind === "rareCandy") {
              return (
                <CandyCard
                  key={item.kind}
                  item={item}
                  head={head}
                  busy={busy}
                  unusableReason={unusableReason}
                  onUse={onUseCandy}
                />
              );
            }

            return (
              <article key={item.kind} className="card">
                {head}
                <div className="card__foot">
                  <span className="dim">
                    {item.kind === "mint" && "Re-rolls nature"}
                    {item.kind === "shinyCharm" && "Shiny odds 1/48"}
                  </span>
                  {item.kind === "mint" && (
                    <button
                      className="btn btn--primary"
                      disabled={busy || !item.usable}
                      onClick={onUseMint}
                      title={item.usable ? undefined : unusableReason}
                    >
                      Use
                    </button>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      </Panel>
    </div>
  );
}

function CandyCard({
  item,
  head,
  busy,
  unusableReason,
  onUse,
}: {
  item: BagItemView;
  head: React.ReactNode;
  busy: boolean;
  unusableReason: string;
  onUse: (count: number) => void;
}) {
  const max = Math.max(1, item.max_use);
  const [selected, setSelected] = useState(1);
  // The maximum moves as the Pokémon grows; keep the choice inside it.
  const n = Math.min(Math.max(1, selected), max);
  const preview = item.previews[n - 1];

  return (
    <article className="card">
      {head}
      {preview && (
        <div className="candy__preview">
          {preview.graduates && <p>Expected to graduate.</p>}
          {preview.discarded > 0 ? (
            <p className="candy__warn">
              On graduation, {tokens(preview.discarded)} leftover XP will be discarded.
            </p>
          ) : (
            preview.evolves &&
            !preview.graduates && (
              <p>Expected after evolution: {tokens(preview.carryover)} XP carried over.</p>
            )
          )}
        </div>
      )}
      <div className="card__foot candy__foot">
        <div className="stepper" role="group" aria-label="Candies to use">
          <button
            className="stepper__btn"
            aria-label="One fewer"
            disabled={n <= 1}
            onClick={() => setSelected(n - 1)}
          >
            −
          </button>
          <span className="stepper__value">×{n}</span>
          <button
            className="stepper__btn"
            aria-label="One more"
            disabled={n >= max}
            onClick={() => setSelected(n + 1)}
          >
            +
          </button>
        </div>
        <span className="dim">+{tokens(n * CANDY_XP)} XP</span>
        <button
          className="btn btn--primary"
          disabled={busy || !item.usable}
          onClick={() => onUse(n)}
          title={item.usable ? undefined : unusableReason}
        >
          Use ×{n}
        </button>
      </div>
    </article>
  );
}
