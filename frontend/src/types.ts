export type Rarity = "common" | "uncommon" | "rare" | "legendary";

export type DisplayState =
  | "egg"
  | "idle"
  | "working"
  | "focus"
  | "tired"
  | "sleep"
  | "levelUp";

export interface ChainSlot {
  species_id: number | null;
  name: string | null;
  reached: boolean;
  is_current: boolean;
  mystery: boolean;
}

export interface ActiveView {
  base_id: number;
  species_id: number;
  name: string;
  rarity: Rarity;
  stage_index: number;
  total_forms: number;
  is_final: boolean;
  is_shiny: boolean;
  nature: string | null;
  nature_label: string | null;
  used_at_stage: number;
  threshold: number;
  progress: number;
  tokens_to_next: number;
  chain: ChainSlot[];
  line_loaded: boolean;
  hatched_at: string | null;
  /** 2 when this line graduated before and grows twice as fast. */
  growth_multiplier: number | null;
  /** Shiny or legendary: sending it off asks twice. */
  is_high_value: boolean;
  level: number | null;
  unown_form: string | null;
}

export interface EggView {
  usage: number;
  threshold: number;
  progress: number;
  tokens_to_hatch: number;
  guaranteed_tier: Rarity | null;
  /** Ready, but the last hatch attempt failed for an external reason. */
  hatch_delayed: boolean;
}

export interface CompanionEvent {
  kind: "hatch" | "evolve" | "graduate" | "ditto_reveal";
  name?: string;
  shiny?: boolean;
  species_id?: number;
  disguise_name?: string;
  /** Shiny odds denominator the hatch actually rolled with. */
  odds?: number;
  form?: string | null;
}

export interface CompanionView {
  display_state: DisplayState;
  egg: EggView;
  active: ActiveView | null;
  event: CompanionEvent | null;
  just_evolved_to: string | null;
  just_graduated: string | null;
}

/** Where a cost came from. Only "unknown without anything known" reads Unavailable. */
export interface CostCoverage {
  reported: boolean;
  estimated: boolean;
  unknown: boolean;
}

export interface PeriodView {
  total_tokens: number;
  cost: number;
  cost_coverage: CostCoverage;
  input: number;
  output: number;
  cache_write: number;
  cache_read: number;
  by_model: Record<string, number>;
}

export interface BlockView {
  start: string;
  end: string;
  total_tokens: number;
  cost: number;
  tokens_per_minute: number;
  cost_coverage: CostCoverage;
}

export interface DayPoint {
  date: string;
  total_tokens: number;
  cost: number;
  cost_coverage: CostCoverage;
}

export interface UsageView {
  today_date: string;
  today: PeriodView;
  week: PeriodView;
  month: PeriodView;
  block: BlockView | null;
  burn_per_minute: number;
  burn_tier: "idle" | "normal" | "fast" | "blazing";
  /** This month from the 1st through today, empty days included. */
  month_daily: DayPoint[];
  scanned_files: number;
}

export interface LimitWindowView {
  key: string;
  name: string;
  kind: string;
  utilization: number;
  resets_at: string | null;
  /** Window length in seconds, for the pace marker. */
  span_seconds: number | null;
}

export interface AccountLimitsView {
  id: string;
  title: string;
  is_default: boolean;
  available: boolean;
  stale: boolean;
  plan: string | null;
  /** "email · org", or just the email for personal plans. */
  account: string | null;
  windows: LimitWindowView[];
  error: string | null;
  auth_expired: boolean;
  fetched_at: string | null;
  folder: string | null;
}

/** The default account at the top level; every account, default first, in `accounts`. */
export interface LimitsView {
  available: boolean;
  enabled: boolean;
  stale: boolean;
  plan: string | null;
  account: string | null;
  windows: LimitWindowView[];
  error: string | null;
  auth_expired: boolean;
  fetched_at: string | null;
  accounts: AccountLimitsView[];
}

export interface WalletView {
  available_tokens: number;
  used_since_install: number;
  spent_tokens: number;
}

export interface BagItemView {
  kind: string;
  label: string;
  count: number;
  emoji: string;
  sprite_name: string | null;
  passive: boolean;
  usable: boolean;
  /** Rare Candy only: how many can be fed at once, and what each count would do. */
  max_use: number;
  previews: CandyPreview[];
}

export interface CandyPreview {
  count: number;
  xp: number;
  evolves: boolean;
  graduates: boolean;
  carryover: number;
  discarded: number;
}

export interface ShopItemView {
  kind: string;
  label: string;
  description: string;
  price: number;
  emoji: string;
  sprite_name: string | null;
  affordable: boolean;
  owned: boolean;
  passive: boolean;
}

export interface ShopEggView {
  tier: Rarity | null;
  label: string;
  description: string;
  price: number;
  affordable: boolean;
  /** False during the egg stage: a shop egg always sends the current Pokémon off. */
  buyable: boolean;
  locked_reason: string | null;
}

export interface ShopView {
  items: ShopItemView[];
  eggs: ShopEggView[];
}

export interface DexChainSpecies {
  species_id: number;
  name: string;
}

/** A catch-log row. The Pokémon being raised appears too, with `is_active`. */
export interface DexEntryView {
  id: string;
  is_active: boolean;
  is_released: boolean;
  base_id: number;
  final_id: number;
  final_name: string;
  rarity: Rarity;
  is_shiny: boolean;
  nature: string | null;
  nature_label: string | null;
  caught_at: string | null;
  level: number | null;
  unown_form: string | null;
  chain: DexChainSpecies[];
  /** Every stored name of every species in the chain, in every language. */
  search_names: string[];
}

/** One Pokédex cell: every species ever reached, including earlier forms. */
export interface DexSpeciesView {
  species_id: number;
  name: string;
  rarity: Rarity;
  count: number;
  is_shiny: boolean;
  has_normal: boolean;
  /** Only the current form of the Pokémon being raised. */
  is_raising: boolean;
  first_caught_at: string | null;
  search_names: string[];
}

export interface UnownFormView {
  form: string;
  symbol: string;
  is_shiny: boolean;
  has_normal: boolean;
  is_raising: boolean;
}

export interface CollectionView {
  total: number;
  counts_by_rarity: Record<string, number>;
  catch_log: DexEntryView[];
  pokedex: DexSpeciesView[];
  unown_forms: UnownFormView[];
}

export interface StatView {
  stat: string;
  base: number;
  iv: number | null;
  value: number | null;
}

export interface MoveView {
  name: string;
  methods: { method: string; level: number }[];
}

export interface IndividualView {
  id: string;
  is_active: boolean;
  is_shiny: boolean;
  caught_at: string | null;
  level: number;
  gender: "male" | "female" | "genderless" | null;
  nature: string | null;
  nature_label: string | null;
  ability_name: string | null;
  ability_is_hidden: boolean;
  unown_form: string | null;
  stats: StatView[];
  stat_scale: number;
  moves: { name: string; learned_at_level: number }[];
}

export interface PokemonDetailView {
  species_id: number;
  name: string;
  rarity: Rarity | null;
  types: string[];
  height_m: number;
  weight_kg: number;
  base_total: number;
  base_stats: StatView[];
  abilities: { name: string; is_hidden: boolean }[];
  moves: MoveView[];
  /** Newest first, the one being raised first. Empty on earlier evolution stages. */
  individuals: IndividualView[];
}

export type RecapScope = "week" | "month" | "year";

export interface RecapView {
  scope: RecapScope;
  offset: number;
  start: string;
  /** Exclusive. */
  end: string;
  total: number;
  buckets: { key: string; tokens: number; is_current: boolean; has_data: boolean }[];
  is_in_progress: boolean;
  previous_total: number | null;
  delta: number | null;
  best_day: string | null;
  best_day_tokens: number;
  active_days: number;
  counted_days: number;
  best_streak: number;
  graduated_count: number;
  graduated: { species_id: number; name: string; is_shiny: boolean; unown_form: string | null }[];
  can_go_back: boolean;
}

export interface SnapshotView {
  id: string;
  created_at: string;
  dex_count: number;
  lifetime_tokens: number;
  current_species_id: number | null;
  current_is_shiny: boolean;
}

export interface MetaView {
  last_refresh: string | null;
  poll_interval: number;
  timezone: string;
  language: string;
  providers: string[];
  log_roots: string[];
  log_roots_present: string[];
  log_files_found: number;
  source_warning: string | null;
  version: string;
  growth_difficulty: number;
  shop_difficulty: number;
  difficulty_min: number;
  difficulty_max: number;
  limit_display: "used" | "remaining";
  crit_threshold: number;
}

export interface StateView {
  companion: CompanionView;
  usage: UsageView;
  limits: LimitsView;
  wallet: WalletView;
  bag: BagItemView[];
  shop: ShopView;
  collection: CollectionView;
  meta: MetaView;
}
