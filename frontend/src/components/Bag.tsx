import { itemSpriteUrl } from "../lib/api";
import { tokens } from "../lib/format";
import { Panel } from "./Primitives";
import type { StateView } from "../types";

export function Bag({
  state,
  busy,
  onUseCandy,
  onUseMint,
}: {
  state: StateView;
  busy: boolean;
  onUseCandy: () => void;
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

  return (
    <div className="stack">
      <Panel title="Bag">
        <div className="cards">
          {bag.map((item) => {
            const handler =
              item.kind === "rareCandy" ? onUseCandy : item.kind === "mint" ? onUseMint : null;
            return (
              <article key={item.kind} className="card">
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
                <div className="card__foot">
                  <span className="dim">
                    {item.kind === "rareCandy" && `+${tokens(100_000_000)} growth`}
                    {item.kind === "mint" && "Re-rolls nature"}
                    {item.kind === "shinyCharm" && "Shiny odds 1/48"}
                  </span>
                  {handler && (
                    <button
                      className="btn btn--primary"
                      disabled={busy || !item.usable}
                      onClick={handler}
                      title={
                        item.usable
                          ? undefined
                          : companion.active
                            ? "Waiting for the evolution line to load"
                            : "You need an active Pokémon"
                      }
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
