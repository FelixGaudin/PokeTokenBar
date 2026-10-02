import "../styles/collection.css";
import { useEffect, useRef, useState } from "react";
import { age, titleCase } from "../lib/format";
import {
  DEX_SORT_DEFAULT,
  DEX_SORTS,
  LOG_SORT_DEFAULT,
  LOG_SORTS,
  UNOWN_ID,
  filterEntries,
  filterSpecies,
  sortEntries,
  sortSpecies,
  type DexFilters,
  type DexSort,
  type LogSort,
} from "../lib/dex";
import { Panel, RarityBadge, ShinyBadge, Sprite } from "./Primitives";
import { PokemonDetail } from "./PokemonDetail";
import type { DexEntryView, DexSpeciesView, Rarity, StateView, UnownFormView } from "../types";

const RARITIES: Rarity[] = ["legendary", "rare", "uncommon", "common"];
const PAGE_SIZE = 16;

type Tab = "pokedex" | "log";

export function Collection({ state }: { state: StateView }) {
  const { collection } = state;
  const lang = state.meta.language;
  const [tab, setTab] = useState<Tab>("pokedex");
  const [query, setQuery] = useState("");
  const [shinyOnly, setShinyOnly] = useState(false);
  const [rarity, setRarity] = useState<Rarity | null>(null);
  const [dexSort, setDexSort] = useState<DexSort>(DEX_SORT_DEFAULT);
  const [logSort, setLogSort] = useState<LogSort>(LOG_SORT_DEFAULT);
  const [openId, setOpenId] = useState<number | null>(null);

  // The page resets to 0 whenever a filter, the search or the sort changes.
  const dexKey = `${query}|${shinyOnly}|${rarity}|${dexSort}`;
  const [pageState, setPageState] = useState({ key: dexKey, page: 0 });
  const logKey = `${query}|${shinyOnly}|${rarity}|${logSort}`;
  const logRef = useRef<HTMLDivElement>(null);
  const lastLogKey = useRef(logKey);

  useEffect(() => {
    if (logKey === lastLogKey.current) return;
    lastLogKey.current = logKey;
    const el = logRef.current;
    if (el && el.getBoundingClientRect().top < 0) el.scrollIntoView({ behavior: "smooth" });
  }, [logKey]);

  if (collection.catch_log.length === 0) {
    return (
      <Panel>
        <div className="collEmpty">
          <Sprite speciesId={25} size={96} alt="Pikachu" />
          <p className="collEmpty__title">No Pokémon caught yet!</p>
          <p className="collEmpty__hint">Spend tokens to hatch your first Pokémon.</p>
        </div>
      </Panel>
    );
  }

  const switchTab = (next: Tab) => {
    setTab(next);
    setOpenId(null);
  };

  const segmented = (
    <div className="segmented" role="tablist">
      <button
        role="tab"
        aria-selected={tab === "pokedex"}
        className={tab === "pokedex" ? "is-active" : ""}
        onClick={() => switchTab("pokedex")}
      >
        Pokédex
      </button>
      <button
        role="tab"
        aria-selected={tab === "log"}
        className={tab === "log" ? "is-active" : ""}
        onClick={() => switchTab("log")}
      >
        Catch log
      </button>
    </div>
  );

  const opened = openId === null ? null : collection.pokedex.find((s) => s.species_id === openId);
  if (opened) {
    return (
      <div className="stack">
        <Panel>{segmented}</Panel>
        <PokemonDetail
          key={opened.species_id}
          species={opened}
          unownForms={collection.unown_forms}
          entries={collection.catch_log}
          language={lang}
          onBack={() => setOpenId(null)}
        />
      </div>
    );
  }

  const filters: DexFilters = { rarity, shinyOnly, query };
  const resetFilters = () => {
    setQuery("");
    setShinyOnly(false);
    setRarity(null);
    setDexSort(DEX_SORT_DEFAULT);
    setLogSort(LOG_SORT_DEFAULT);
  };

  const counts: Record<string, number> = {};
  for (const item of tab === "pokedex" ? collection.pokedex : collection.catch_log) {
    counts[item.rarity] = (counts[item.rarity] ?? 0) + 1;
  }

  const sortOptions = tab === "pokedex" ? DEX_SORTS : LOG_SORTS;
  const sortValue = tab === "pokedex" ? dexSort : logSort;
  const sortModified =
    tab === "pokedex" ? dexSort !== DEX_SORT_DEFAULT : logSort !== LOG_SORT_DEFAULT;
  const filtering = query.trim() !== "" || shinyOnly || rarity !== null;

  const toolbar = (
    <>
      <div className="collToolbar">
        <label className="collSearch">
          <span className="collSearch__icon" aria-hidden="true">
            🔍
          </span>
          <input
            type="search"
            value={query}
            placeholder="Search name or #…"
            aria-label="Search name or #…"
            onChange={(e) => setQuery(e.target.value)}
          />
          {query !== "" && (
            <button
              type="button"
              className="collSearch__clear"
              aria-label="Clear search"
              onClick={() => setQuery("")}
            >
              ×
            </button>
          )}
        </label>
        <button
          type="button"
          className={`collShiny ${shinyOnly ? "is-active" : ""}`}
          aria-pressed={shinyOnly}
          aria-label="Shiny only"
          title="Shiny only"
          onClick={() => setShinyOnly((v) => !v)}
        >
          ✨
        </button>
        <select
          className={`collSort ${sortModified ? "is-modified" : ""}`}
          aria-label="Sort"
          title="Sort"
          value={sortValue}
          onChange={(e) =>
            tab === "pokedex"
              ? setDexSort(e.target.value as DexSort)
              : setLogSort(e.target.value as LogSort)
          }
        >
          {sortOptions.map((o) => (
            <option key={o.key} value={o.key}>
              {o.label}
            </option>
          ))}
        </select>
      </div>
      <div className="collChips">
        {RARITIES.map((r) => {
          const count = counts[r] ?? 0;
          const selected = rarity === r;
          return (
            <button
              key={r}
              type="button"
              className={`chip chip--${r} ${selected ? "is-active" : ""}`}
              aria-pressed={selected}
              disabled={count === 0 && !selected}
              title="Tap to show only this rarity · tap again to clear"
              onClick={() => setRarity(selected ? null : r)}
            >
              <span className={`collChips__dot collChips__dot--${r}`} aria-hidden="true" />
              {r} <strong>{count}</strong>
            </button>
          );
        })}
      </div>
    </>
  );

  const noResults = (
    <div className="collNoResults">
      <span className="collNoResults__icon" aria-hidden="true">
        🔍
      </span>
      <p>No Pokémon found</p>
      <button type="button" className="btn btn--ghost" onClick={resetFilters}>
        Reset filters
      </button>
    </div>
  );

  if (tab === "pokedex") {
    const dex = sortSpecies(filterSpecies(collection.pokedex, filters), dexSort, lang);
    const pages = Math.max(1, Math.ceil(dex.length / PAGE_SIZE));
    const page = Math.min(pageState.key === dexKey ? pageState.page : 0, pages - 1);
    const setPage = (p: number) => setPageState({ key: dexKey, page: p });
    const slice = dex.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
    // Keep every page the same height once there is more than one.
    const fillers = pages > 1 ? PAGE_SIZE - slice.length : 0;

    return (
      <div className="stack">
        <Panel>
          {segmented}
          {toolbar}
        </Panel>
        <Panel
          className="collPanel"
          title={`Pokédex · ${collection.pokedex.length} species`}
          aside={filtering ? `${dex.length} shown` : undefined}
        >
          {dex.length === 0 ? (
            noResults
          ) : (
            <>
              <div className="collGrid">
                {slice.map((s) => (
                  <DexCell
                    key={s.species_id}
                    species={s}
                    unownForms={collection.unown_forms}
                    onOpen={() => setOpenId(s.species_id)}
                  />
                ))}
                {Array.from({ length: fillers }, (_, i) => (
                  <span key={`filler-${i}`} className="collCell collCell--filler" aria-hidden="true" />
                ))}
              </div>
              {pages > 1 && (
                <nav className="collPager">
                  <button
                    type="button"
                    aria-label="Previous page"
                    disabled={page === 0}
                    onClick={() => setPage(page - 1)}
                  >
                    ‹
                  </button>
                  <span aria-label={`Page ${page + 1} of ${pages}`}>
                    {page + 1} / {pages}
                  </span>
                  <button
                    type="button"
                    aria-label="Next page"
                    disabled={page >= pages - 1}
                    onClick={() => setPage(page + 1)}
                  >
                    ›
                  </button>
                </nav>
              )}
            </>
          )}
        </Panel>
      </div>
    );
  }

  const log = sortEntries(filterEntries(collection.catch_log, filters), logSort, lang);
  return (
    <div className="stack">
      <Panel>
        {segmented}
        {toolbar}
      </Panel>
      <div ref={logRef}>
        <Panel
          className="collPanel"
          title={`Catch log · ${collection.catch_log.length} total`}
          aside={filtering ? `${log.length} shown` : undefined}
        >
          {log.length === 0 ? (
            noResults
          ) : (
            <div className="collLog">
              {log.map((entry) => (
                <LogRow key={entry.id} entry={entry} />
              ))}
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}

function DexCell({
  species,
  unownForms,
  onOpen,
}: {
  species: DexSpeciesView;
  unownForms: UnownFormView[];
  onOpen: () => void;
}) {
  const isUnown = species.species_id === UNOWN_ID;
  const form: UnownFormView | undefined = isUnown ? unownForms[0] : undefined;
  const shiny = form ? !form.has_normal : !species.has_normal;
  const caption = isUnown ? `${species.name} ${unownForms.length}/28` : species.name;
  const tooltip = [
    `#${species.species_id} ${species.name}`,
    titleCase(species.rarity),
    species.is_shiny ? "Shiny" : null,
    species.is_raising ? "Raising" : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <button type="button" className="collCell" title={tooltip} onClick={onOpen}>
      <span className={`collCell__num collCell__num--${species.rarity}`}>
        #{species.species_id}
      </span>
      {species.is_shiny && (
        <span className="collCell__shiny" aria-label="Shiny collected">
          ✨
        </span>
      )}
      <span className="collCell__art">
        <Sprite
          speciesId={species.species_id}
          shiny={shiny}
          animated={false}
          size={56}
          form={form?.form ?? null}
          alt=""
        />
        {species.is_raising && <span className="collCell__raising">Raising</span>}
      </span>
      <span className="collCell__name">{caption}</span>
    </button>
  );
}

function LogRow({ entry }: { entry: DexEntryView }) {
  return (
    <article className="collRow">
      <header className="collRow__head">
        <RarityBadge rarity={entry.rarity} />
        {entry.is_shiny && <ShinyBadge />}
        {entry.is_active && <span className="badge collBadge--raising">Raising</span>}
        {entry.is_released && <span className="badge collBadge--released">Released</span>}
        <span className="collRow__spacer" />
        {entry.level !== null && <span className="collRow__level">Lv. {entry.level}</span>}
        {entry.nature_label && <span className="nature">{entry.nature_label}</span>}
      </header>
      <div className="collRow__chain">
        {entry.chain.map((sp, i) => (
          <div key={`${sp.species_id}-${i}`} className="collRow__item">
            {i > 0 && (
              <span className="collRow__arrow" aria-hidden="true">
                →
              </span>
            )}
            <div className="collRow__slot">
              <Sprite
                speciesId={sp.species_id}
                shiny={entry.is_shiny}
                animated={false}
                size={68}
                form={sp.species_id === UNOWN_ID ? entry.unown_form : null}
                alt=""
              />
              <span className="collRow__name">{sp.name}</span>
            </div>
          </div>
        ))}
      </div>
      {entry.caught_at && <footer className="collRow__foot">{age(entry.caught_at)}</footer>}
    </article>
  );
}
