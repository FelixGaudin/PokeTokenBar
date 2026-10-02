import type { DexEntryView, DexSpeciesView, Rarity } from "../types";

export type DexSort = "numberAsc" | "numberDesc" | "nameAsc" | "nameDesc" | "rarityDesc";
export type LogSort =
  | "recentFirst"
  | "oldestFirst"
  | "numberAsc"
  | "numberDesc"
  | "nameAsc"
  | "nameDesc"
  | "rarityDesc";

export const DEX_SORT_DEFAULT: DexSort = "numberAsc";
export const LOG_SORT_DEFAULT: LogSort = "recentFirst";

export const DEX_SORTS: { key: DexSort; label: string }[] = [
  { key: "numberAsc", label: "Number (Low to High)" },
  { key: "numberDesc", label: "Number (High to Low)" },
  { key: "nameAsc", label: "Name (A–Z)" },
  { key: "nameDesc", label: "Name (Z–A)" },
  { key: "rarityDesc", label: "Rarity (High to Low)" },
];

export const LOG_SORTS: { key: LogSort; label: string }[] = [
  { key: "recentFirst", label: "Most Recent" },
  { key: "oldestFirst", label: "Oldest First" },
  { key: "numberAsc", label: "Number (Low to High)" },
  { key: "numberDesc", label: "Number (High to Low)" },
  { key: "nameAsc", label: "Name (A–Z)" },
  { key: "nameDesc", label: "Name (Z–A)" },
  { key: "rarityDesc", label: "Rarity (High to Low)" },
];

export const RARITY_RANK: Record<Rarity, number> = {
  common: 0,
  uncommon: 1,
  rare: 2,
  legendary: 3,
};

export interface DexFilters {
  rarity: Rarity | null;
  shinyOnly: boolean;
  query: string;
}

/**
 * Case- and diacritic-insensitive. Only the combining-diacritics block is stripped and the
 * result recomposed, so Hangul syllables survive NFD and still match as substrings.
 */
export function fold(s: string): string {
  return s.normalize("NFD").replace(/[̀-ͯ]/g, "").normalize("NFC").toLocaleLowerCase();
}

export function matchesQuery(rawQuery: string, ids: number[], names: string[]): boolean {
  const q = rawQuery.trim();
  if (q === "") return true;
  const digits = q.startsWith("#") ? q.slice(1) : q;
  const number = /^\d+$/.test(digits) ? Number(digits) : null;
  if (number !== null && ids.includes(number)) return true;
  // Partial numbers: "25" finds #125, "#25" finds #25 and #250.
  if (ids.some((id) => `#${id}`.includes(q))) return true;
  const needle = fold(q);
  return names.some((name) => fold(name).includes(needle));
}

export function speciesMatches(s: DexSpeciesView, query: string): boolean {
  return matchesQuery(query, [s.species_id], [s.name, ...s.search_names]);
}

export function entryMatches(e: DexEntryView, query: string): boolean {
  const chainIds = e.chain.map((c) => c.species_id);
  const names = [e.final_name, ...e.chain.map((c) => c.name), ...e.search_names];
  return matchesQuery(query, [e.base_id, e.final_id, ...chainIds], names);
}

export function filterSpecies(list: DexSpeciesView[], f: DexFilters): DexSpeciesView[] {
  return list.filter(
    (s) =>
      (f.rarity === null || s.rarity === f.rarity) &&
      (!f.shinyOnly || s.is_shiny) &&
      speciesMatches(s, f.query),
  );
}

export function filterEntries(list: DexEntryView[], f: DexFilters): DexEntryView[] {
  return list.filter(
    (e) =>
      (f.rarity === null || e.rarity === f.rarity) &&
      (!f.shinyOnly || e.is_shiny) &&
      entryMatches(e, f.query),
  );
}

function collator(lang: string): Intl.Collator {
  try {
    return new Intl.Collator(lang || undefined);
  } catch {
    return new Intl.Collator();
  }
}

export function sortSpecies(list: DexSpeciesView[], sort: DexSort, lang = "en"): DexSpeciesView[] {
  const coll = collator(lang);
  const byId = (a: DexSpeciesView, b: DexSpeciesView) => a.species_id - b.species_id;
  const cmp: Record<DexSort, (a: DexSpeciesView, b: DexSpeciesView) => number> = {
    numberAsc: byId,
    numberDesc: (a, b) => b.species_id - a.species_id,
    nameAsc: (a, b) => coll.compare(a.name, b.name) || byId(a, b),
    nameDesc: (a, b) => coll.compare(b.name, a.name) || byId(a, b),
    rarityDesc: (a, b) => RARITY_RANK[b.rarity] - RARITY_RANK[a.rarity] || byId(a, b),
  };
  return [...list].sort(cmp[sort]);
}

/** A missing date counts as the distant past. */
function caughtTime(e: DexEntryView): number {
  if (!e.caught_at) return -Infinity;
  const t = Date.parse(e.caught_at);
  return Number.isNaN(t) ? -Infinity : t;
}

function byIdAsc(a: DexEntryView, b: DexEntryView): number {
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

function timeDesc(a: DexEntryView, b: DexEntryView): number {
  const ta = caughtTime(a);
  const tb = caughtTime(b);
  return ta === tb ? 0 : tb > ta ? 1 : -1;
}

/** Tie-break shared by the non-date sorts: newest first, then id. */
function tieBreak(a: DexEntryView, b: DexEntryView): number {
  return timeDesc(a, b) || byIdAsc(a, b);
}

export function sortEntries(list: DexEntryView[], sort: LogSort, lang = "en"): DexEntryView[] {
  if (sort === "recentFirst" || sort === "oldestFirst") {
    const active = list.filter((e) => e.is_active);
    const rest = list.filter((e) => !e.is_active);
    if (sort === "recentFirst") {
      return [...active, ...rest.sort((a, b) => timeDesc(a, b) || byIdAsc(a, b))];
    }
    return [...rest.sort((a, b) => timeDesc(b, a) || byIdAsc(a, b)), ...active];
  }
  const coll = collator(lang);
  const cmp: Record<Exclude<LogSort, "recentFirst" | "oldestFirst">, (a: DexEntryView, b: DexEntryView) => number> = {
    numberAsc: (a, b) => a.final_id - b.final_id || tieBreak(a, b),
    numberDesc: (a, b) => b.final_id - a.final_id || tieBreak(a, b),
    nameAsc: (a, b) => coll.compare(a.final_name, b.final_name) || tieBreak(a, b),
    nameDesc: (a, b) => coll.compare(b.final_name, a.final_name) || tieBreak(a, b),
    rarityDesc: (a, b) => RARITY_RANK[b.rarity] - RARITY_RANK[a.rarity] || tieBreak(a, b),
  };
  return [...list].sort(cmp[sort]);
}

/** "special-attack" -> "Special Attack". */
export function slugLabel(slug: string): string {
  return slug
    .split("-")
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

export const STAT_LABELS: Record<string, string> = {
  hp: "HP",
  attack: "Attack",
  defense: "Defense",
  "special-attack": "Sp. Atk",
  "special-defense": "Sp. Def",
  speed: "Speed",
};

export function moveMethodLabel(method: string, level: number): string {
  switch (method) {
    case "level-up":
      return level === 0 ? "Start" : `Lv. ${level}`;
    case "machine":
      return "TM";
    case "egg":
      return "Egg";
    case "tutor":
      return "Tutor";
    default:
      return method.replace(/-/g, " ");
  }
}

export function moveMethodsText(methods: { method: string; level: number }[]): string {
  return [...new Set(methods.map((m) => moveMethodLabel(m.method, m.level)))].join(" · ");
}

export const UNOWN_ID = 201;

export const UNOWN_FORMS: { form: string; symbol: string }[] = [
  ..."abcdefghijklmnopqrstuvwxyz".split("").map((c) => ({ form: c, symbol: c.toUpperCase() })),
  { form: "exclamation", symbol: "!" },
  { form: "question", symbol: "?" },
];

export function unownSymbol(form: string | null | undefined): string | null {
  return UNOWN_FORMS.find((f) => f.form === form)?.symbol ?? null;
}
