import "../styles/collection.css";
import { useEffect, useRef, useState } from "react";
import { age, titleCase } from "../lib/format";
import {
  DEX_SORT_DEFAULT,
  DEX_SORTS,
  LOG_SORT_DEFAULT,
  LOG_SORTS,
  collectedForms,
  filterEntries,
  filterSpecies,
  sortEntries,
  sortSpecies,
  type DexFilters,
  type DexSort,
  type LogSort,
} from "../lib/dex";
import { DexLink, Panel, RarityBadge, ShinyBadge, Sprite } from "./Primitives";
import { PokemonDetail } from "./PokemonDetail";
import type {
  DexBadgeView,
  DexEntryView,
  DexSpeciesView,
  Rarity,
  RegionProgressView,
  StateView,
} from "../types";

const RARITIES: Rarity[] = ["legendary", "rare", "uncommon", "common"];
const PAGE_SIZE = 16;

type Tab = "pokedex" | "log";

export function Collection({
  state,
  openId,
  onOpen,
}: {
  state: StateView;
  /** The open Pokédex entry (species number), or null for the grid / log. */
  openId: number | null;
  onOpen: (speciesId: number | null) => void;
}) {
  const { collection } = state;
  const lang = state.meta.language;
  const [tab, setTab] = useState<Tab>("pokedex");
  const [query, setQuery] = useState("");
  const [shinyOnly, setShinyOnly] = useState(false);
  const [rarity, setRarity] = useState<Rarity | null>(null);
  // Collection-level: kept across a switch to the catch log, which ignores it.
  const [regionKey, setRegionKey] = useState<string | null>(null);
  const [dexSort, setDexSort] = useState<DexSort>(DEX_SORT_DEFAULT);
  const [logSort, setLogSort] = useState<LogSort>(LOG_SORT_DEFAULT);

  // The page resets to 0 whenever a filter, the search or the sort changes.
  const dexKey = `${query}|${shinyOnly}|${rarity}|${regionKey}|${dexSort}`;
  const [pageState, setPageState] = useState({ key: dexKey, page: 0 });
  const logKey = `${query}|${shinyOnly}|${rarity}|${logSort}`;
  const logRef = useRef<HTMLDivElement>(null);
  const lastLogKey = useRef(logKey);
  // The catch log row whose link opened the detail page; Back scrolls it into view once.
  const returnRow = useRef<string | null>(null);

  useEffect(() => {
    if (logKey === lastLogKey.current) return;
    lastLogKey.current = logKey;
    const el = logRef.current;
    if (el && el.getBoundingClientRect().top < 0) el.scrollIntoView({ behavior: "smooth" });
  }, [logKey]);

  useEffect(() => {
    if (openId !== null || tab !== "log" || returnRow.current === null) return;
    const row = logRef.current?.querySelector(`[data-entry="${CSS.escape(returnRow.current)}"]`);
    returnRow.current = null;
    row?.scrollIntoView({ block: "center" });
  }, [openId, tab]);

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
    returnRow.current = null;
    setTab(next);
    onOpen(null);
  };

  const progress = collection.dex_progress;
  const region = progress.regions.find((r) => r.key === regionKey) ?? null;
  const filters: DexFilters = { rarity, shinyOnly, query, region };

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
          entries={collection.catch_log}
          language={lang}
          onBack={() => {
            // Land on the grid page that shows this species, also when it was opened from Home.
            if (tab === "pokedex") {
              const dex = sortSpecies(filterSpecies(collection.pokedex, filters), dexSort, lang);
              const index = dex.findIndex((s) => s.species_id === opened.species_id);
              setPageState({ key: dexKey, page: index < 0 ? 0 : Math.floor(index / PAGE_SIZE) });
            }
            onOpen(null);
          }}
        />
      </div>
    );
  }

  const resetFilters = () => {
    setQuery("");
    setShinyOnly(false);
    setRarity(null);
    setRegionKey(null);
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
  const filtering =
    query.trim() !== "" || shinyOnly || rarity !== null || (tab === "pokedex" && region !== null);

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
      {tab === "pokedex" && (
        <div className="collChips" role="group" aria-label="Regions">
          {progress.regions.map((r) => (
            <RegionChip
              key={r.key}
              region={r}
              selected={r.key === regionKey}
              onToggle={() => setRegionKey(r.key === regionKey ? null : r.key)}
            />
          ))}
        </div>
      )}
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
          aside={
            <>
              <Medals badges={progress.badges} />
              {filtering && `${dex.length} shown`}
            </>
          }
        >
          {dex.length === 0 ? (
            noResults
          ) : (
            <>
              <WheelPagedGrid page={page} pages={pages} onPage={setPage}>
                {slice.map((s) => (
                  <DexCell key={s.species_id} species={s} onOpen={() => onOpen(s.species_id)} />
                ))}
                {Array.from({ length: fillers }, (_, i) => (
                  <span key={`filler-${i}`} className="collCell collCell--filler" aria-hidden="true" />
                ))}
              </WheelPagedGrid>
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
  const dexIds = new Set(collection.pokedex.map((s) => s.species_id));
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
                <LogRow
                  key={entry.id}
                  entry={entry}
                  dexIds={dexIds}
                  onOpen={(speciesId) => {
                    returnRow.current = entry.id;
                    onOpen(speciesId);
                  }}
                />
              ))}
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}

/** Owned / total for a region, with its completion glyph. The counts ignore every filter. */
function RegionChip({
  region,
  selected,
  onToggle,
}: {
  region: RegionProgressView;
  selected: boolean;
  onToggle: () => void;
}) {
  const glyph = region.is_shiny_complete ? "✨" : region.is_complete ? "🏅" : null;
  const label = `${region.name} ${region.owned}/${region.total}`;
  return (
    <button
      type="button"
      className={`chip chip--region ${region.is_complete ? "is-complete" : ""} ${
        selected ? "is-active" : ""
      }`}
      aria-pressed={selected}
      aria-label={label}
      title={label}
      disabled={region.owned === 0 && !selected}
      onClick={onToggle}
    >
      {glyph && (
        <span className="chip__glyph" aria-hidden="true">
          {glyph}
        </span>
      )}
      {region.name} <strong>{`${region.owned}/${region.total}`}</strong>
    </button>
  );
}

/** Earned badges the region chips don't already show: Unown and the national dex. */
function Medals({ badges }: { badges: DexBadgeView[] }) {
  const shown = badges.filter((b) => b.region === null);
  if (shown.length === 0) return null;
  return (
    <span className="collMedals">
      {shown.map((b) => (
        <span key={b.key} className="collMedal" title={b.label} role="img" aria-label={b.label}>
          {b.emoji}
        </span>
      ))}
    </span>
  );
}

/** Wheel distance (px) a gesture needs before it flips the page. */
const WHEEL_THRESHOLD = 24;
/** A pause this long (ms) ends a wheel gesture, momentum tail included. */
const WHEEL_GESTURE_GAP = 200;

/**
 * The Pokédex grid, paged by the scroll wheel: one page per gesture, its momentum tail
 * ignored. At the first or last page the wheel scrolls the page as usual.
 */
function WheelPagedGrid({
  page,
  pages,
  onPage,
  children,
}: {
  page: number;
  pages: number;
  onPage: (page: number) => void;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const latest = useRef({ page, pages, onPage });
  useEffect(() => {
    latest.current = { page, pages, onPage };
  });

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let accumulated = 0;
    let fired = false;
    let lastEvent = 0;
    const onWheel = (e: WheelEvent) => {
      const { page: current, pages: count, onPage: go } = latest.current;
      if (e.timeStamp - lastEvent > WHEEL_GESTURE_GAP) {
        accumulated = 0;
        fired = false;
      }
      // The rest of a gesture that already paged, momentum included, stays here.
      if (fired) {
        e.preventDefault();
        lastEvent = e.timeStamp;
        return;
      }
      const step = e.deltaY > 0 ? 1 : e.deltaY < 0 ? -1 : 0;
      const next = current + step;
      if (step === 0 || next < 0 || next >= count) return;
      e.preventDefault();
      lastEvent = e.timeStamp;
      // Lines (Firefox) and pages count as one full notch each.
      accumulated += e.deltaMode === 0 ? e.deltaY : step * WHEEL_THRESHOLD;
      if (Math.abs(accumulated) < WHEEL_THRESHOLD) return;
      fired = true;
      go(next);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  return (
    <div className="collGrid" ref={ref}>
      {children}
    </div>
  );
}

function DexCell({ species, onOpen }: { species: DexSpeciesView; onOpen: () => void }) {
  const collected = collectedForms(species.forms);
  const form = collected[0];
  const shiny = form ? !form.has_normal : species.is_shiny;
  const caption =
    species.forms.length > 0
      ? `${species.name} ${collected.length}/${species.forms.length}`
      : species.name;
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

function LogRow({
  entry,
  dexIds,
  onOpen,
}: {
  entry: DexEntryView;
  dexIds: Set<number>;
  onOpen: (speciesId: number) => void;
}) {
  return (
    <article className="collRow" data-entry={entry.id}>
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
            <DexLink
              speciesId={dexIds.has(sp.species_id) ? sp.species_id : null}
              onOpen={onOpen}
              name={sp.name}
              className="collRow__slot"
            >
              <Sprite
                speciesId={sp.species_id}
                shiny={entry.is_shiny}
                animated={false}
                size={68}
                form={entry.form}
                alt=""
              />
              <span className="collRow__name">{sp.name}</span>
            </DexLink>
          </div>
        ))}
      </div>
      {entry.caught_at && <footer className="collRow__foot">{age(entry.caught_at)}</footer>}
    </article>
  );
}
