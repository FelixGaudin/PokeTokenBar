import { useState } from "react";
import { age } from "../lib/format";
import { Panel, RarityBadge, Sprite } from "./Primitives";
import type { Rarity, StateView } from "../types";

const RARITIES: Rarity[] = ["legendary", "rare", "uncommon", "common"];

export function Collection({ state }: { state: StateView }) {
  const { collection } = state;
  const [tab, setTab] = useState<"pokedex" | "log">("pokedex");
  const [filter, setFilter] = useState<Rarity | null>(null);

  if (collection.total === 0) {
    return (
      <Panel title="Collection">
        <p className="empty">
          Nothing here yet. A Pokémon joins your Pokédex when it reaches its final form
          and graduates.
        </p>
      </Panel>
    );
  }

  const log = filter
    ? collection.catch_log.filter((e) => e.rarity === filter)
    : collection.catch_log;
  const dex = filter ? collection.pokedex.filter((e) => e.rarity === filter) : collection.pokedex;

  return (
    <div className="stack">
      <Panel>
        <div className="segmented" role="tablist">
          <button
            role="tab"
            aria-selected={tab === "pokedex"}
            className={tab === "pokedex" ? "is-active" : ""}
            onClick={() => setTab("pokedex")}
          >
            Pokédex
          </button>
          <button
            role="tab"
            aria-selected={tab === "log"}
            className={tab === "log" ? "is-active" : ""}
            onClick={() => setTab("log")}
          >
            Catch log
          </button>
        </div>

        <div className="filters">
          <button
            className={`chip ${filter === null ? "is-active" : ""}`}
            onClick={() => setFilter(null)}
          >
            All {collection.total}
          </button>
          {RARITIES.map((r) => (
            <button
              key={r}
              className={`chip chip--${r} ${filter === r ? "is-active" : ""}`}
              onClick={() => setFilter(filter === r ? null : r)}
            >
              {r} {collection.counts_by_rarity[r] ?? 0}
            </button>
          ))}
        </div>
      </Panel>

      {tab === "pokedex" ? (
        <Panel title={`Pokédex · ${dex.length} species`}>
          {dex.length === 0 ? (
            <p className="empty">No {filter} species yet.</p>
          ) : (
            <div className="dexGrid">
              {dex.map((entry) => (
                <article key={entry.final_id} className="dexCell">
                  <Sprite
                    speciesId={entry.final_id}
                    shiny={entry.shiny_count > 0}
                    animated={false}
                    size={72}
                    alt={entry.name}
                  />
                  <span className="dexCell__name">{entry.name}</span>
                  <span className="dexCell__meta">
                    <RarityBadge rarity={entry.rarity} />
                    {entry.count > 1 && <span className="dim">×{entry.count}</span>}
                    {entry.shiny_count > 0 && <span className="shinyMark">✨</span>}
                  </span>
                </article>
              ))}
            </div>
          )}
        </Panel>
      ) : (
        <Panel title={`Catch log · ${log.length} total`}>
          {log.length === 0 ? (
            <p className="empty">No {filter} catches yet.</p>
          ) : (
            <div className="logList">
              {log.map((entry, index) => (
                <article key={`${entry.final_id}-${entry.caught_at}-${index}`} className="logRow">
                  <header className="logRow__head">
                    <RarityBadge rarity={entry.rarity} />
                    {entry.is_shiny && <span className="badge badge--shiny">✨ Shiny</span>}
                    <span className="logRow__spacer" />
                    {entry.nature_label && <span className="nature">{entry.nature_label}</span>}
                  </header>
                  <div className="logRow__chain">
                    {entry.chain.map((sp, i) => (
                      <div key={sp.species_id} className="chain__item">
                        {i > 0 && (
                          <span className="chain__arrow" aria-hidden="true">
                            →
                          </span>
                        )}
                        <div className="chain__slot">
                          <Sprite
                            speciesId={sp.species_id}
                            shiny={entry.is_shiny}
                            animated={false}
                            size={52}
                            alt={sp.name}
                          />
                          <span className="chain__name">{sp.name}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                  <footer className="logRow__foot dim">{age(entry.caught_at)}</footer>
                </article>
              ))}
            </div>
          )}
        </Panel>
      )}
    </div>
  );
}
