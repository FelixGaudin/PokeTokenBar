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
}

export interface EggView {
  usage: number;
  threshold: number;
  progress: number;
  tokens_to_hatch: number;
  guaranteed_tier: Rarity | null;
}

export interface CompanionEvent {
  kind: "hatch" | "evolve" | "graduate" | "ditto_reveal";
  name?: string;
  shiny?: boolean;
  species_id?: number;
  disguise_name?: string;
}

export interface CompanionView {
  display_state: DisplayState;
  egg: EggView;
  active: ActiveView | null;
  event: CompanionEvent | null;
  just_evolved_to: string | null;
  just_graduated: string | null;
}

export interface PeriodView {
  total_tokens: number;
  cost: number;
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
}

export interface DayPoint {
  date: string;
  total_tokens: number;
  cost: number;
}

export interface UsageView {
  today_date: string;
  today: PeriodView;
  week: PeriodView;
  month: PeriodView;
  block: BlockView | null;
  burn_per_minute: number;
  burn_tier: "idle" | "normal" | "fast" | "blazing";
  daily_history: DayPoint[];
  scanned_files: number;
}

export interface LimitWindowView {
  key: string;
  name: string;
  kind: string;
  utilization: number;
  resets_at: string | null;
}

export interface LimitsView {
  available: boolean;
  enabled: boolean;
  source: string | null;
  plan: string | null;
  windows: LimitWindowView[];
  error: string | null;
  auth_expired: boolean;
  fetched_at: string | null;
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
}

export interface ShopView {
  items: ShopItemView[];
  eggs: ShopEggView[];
}

export interface DexChainSpecies {
  species_id: number;
  name: string;
}

export interface DexEntryView {
  base_id: number;
  final_id: number;
  final_name: string;
  rarity: Rarity;
  is_shiny: boolean;
  nature: string | null;
  nature_label: string | null;
  caught_at: string | null;
  chain: DexChainSpecies[];
}

export interface DexSpeciesView {
  final_id: number;
  name: string;
  rarity: Rarity;
  count: number;
  shiny_count: number;
  first_caught_at: string | null;
}

export interface CollectionView {
  total: number;
  counts_by_rarity: Record<string, number>;
  catch_log: DexEntryView[];
  pokedex: DexSpeciesView[];
}

export interface MetaView {
  last_refresh: string | null;
  poll_interval: number;
  timezone: string;
  language: string;
  providers: string[];
  log_roots: string[];
  version: string;
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
