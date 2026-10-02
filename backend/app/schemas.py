"""Typed API responses. The frontend is generated against these shapes."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .balance import Rarity


class ChainSlot(BaseModel):
    """One form in the active Pokémon's route. Unreached forms stay hidden."""

    species_id: int | None
    name: str | None
    reached: bool
    is_current: bool
    mystery: bool


class ActiveView(BaseModel):
    base_id: int
    species_id: int
    name: str
    rarity: Rarity
    stage_index: int
    total_forms: int
    is_final: bool
    is_shiny: bool
    nature: str | None
    nature_label: str | None
    used_at_stage: int
    threshold: int
    progress: float
    tokens_to_next: int
    chain: list[ChainSlot] = Field(default_factory=list)
    line_loaded: bool
    hatched_at: str | None = None
    # 2 when this line graduated before and grows twice as fast, else None.
    growth_multiplier: int | None = None
    # Shiny or legendary: sending it off asks twice.
    is_high_value: bool = False
    level: int | None = None
    unown_form: str | None = None


class EggView(BaseModel):
    usage: int
    threshold: int
    progress: float
    tokens_to_hatch: int
    guaranteed_tier: Rarity | None = None
    # Ready, but the last hatch attempt failed for an external reason.
    hatch_delayed: bool = False


class CompanionView(BaseModel):
    display_state: str
    egg: EggView
    active: ActiveView | None
    event: dict | None = None
    just_evolved_to: str | None = None
    just_graduated: str | None = None


class CostCoverageView(BaseModel):
    """Where a cost came from. Only "unknown without anything known" reads Unavailable."""

    reported: bool = False
    estimated: bool = False
    unknown: bool = False


class PeriodView(BaseModel):
    total_tokens: int
    cost: float
    cost_coverage: CostCoverageView = Field(default_factory=CostCoverageView)
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0
    by_model: dict[str, int] = Field(default_factory=dict)


class BlockView(BaseModel):
    start: str
    end: str
    total_tokens: int
    cost: float
    tokens_per_minute: float
    cost_coverage: CostCoverageView = Field(default_factory=CostCoverageView)


class DayPoint(BaseModel):
    date: str
    total_tokens: int
    cost: float
    cost_coverage: CostCoverageView = Field(default_factory=CostCoverageView)


class UsageView(BaseModel):
    today_date: str
    today: PeriodView
    week: PeriodView
    month: PeriodView
    block: BlockView | None
    burn_per_minute: float
    burn_tier: str
    # This month, from the 1st through today, empty days included.
    month_daily: list[DayPoint] = Field(default_factory=list)
    scanned_files: int = 0


class LimitWindowView(BaseModel):
    key: str
    name: str
    kind: str
    utilization: float
    resets_at: str | None = None
    # Window length, for the pace marker. None when the kind is unknown.
    span_seconds: int | None = None


class AccountLimitsView(BaseModel):
    """One Claude login's official limits."""

    id: str
    title: str
    is_default: bool
    available: bool
    stale: bool = False
    plan: str | None = None
    account: str | None = None
    windows: list[LimitWindowView] = Field(default_factory=list)
    error: str | None = None
    auth_expired: bool = False
    fetched_at: str | None = None
    folder: str | None = None


class LimitsView(BaseModel):
    """The default account at the top level; every account, default first, in `accounts`."""

    available: bool
    enabled: bool
    # True when these are the last good numbers and the latest fetch failed.
    stale: bool = False
    plan: str | None = None
    account: str | None = None
    windows: list[LimitWindowView] = Field(default_factory=list)
    error: str | None = None
    auth_expired: bool = False
    fetched_at: str | None = None
    accounts: list[AccountLimitsView] = Field(default_factory=list)


class WalletView(BaseModel):
    available_tokens: int
    used_since_install: int
    spent_tokens: int


class BagItemView(BaseModel):
    kind: str
    label: str
    count: int
    emoji: str
    sprite_name: str | None
    passive: bool
    usable: bool
    # Rare Candy only: how many can be fed at once, and what each count would do.
    max_use: int = 0
    previews: list["CandyPreviewView"] = Field(default_factory=list)


class CandyPreviewView(BaseModel):
    count: int
    xp: int
    evolves: bool
    graduates: bool
    carryover: int
    discarded: int


class ShopItemView(BaseModel):
    kind: str
    label: str
    description: str
    price: int
    emoji: str
    sprite_name: str | None
    affordable: bool
    owned: bool
    passive: bool


class ShopEggView(BaseModel):
    tier: Rarity | None
    label: str
    description: str
    price: int
    affordable: bool
    # False during the egg stage: a shop egg always sends the current Pokémon off.
    buyable: bool = True
    locked_reason: str | None = None


class ShopView(BaseModel):
    items: list[ShopItemView] = Field(default_factory=list)
    eggs: list[ShopEggView] = Field(default_factory=list)


class DexChainSpecies(BaseModel):
    species_id: int
    name: str


class DexEntryView(BaseModel):
    """A catch-log row. The Pokémon being raised appears too, with `is_active`."""

    id: str
    is_active: bool = False
    is_released: bool = False
    base_id: int
    final_id: int
    final_name: str
    rarity: Rarity
    is_shiny: bool
    nature: str | None
    nature_label: str | None
    caught_at: str | None
    level: int | None = None
    unown_form: str | None = None
    chain: list[DexChainSpecies] = Field(default_factory=list)
    # Every stored name of every species in the chain, for multilingual search.
    search_names: list[str] = Field(default_factory=list)


class DexSpeciesView(BaseModel):
    """One Pokédex cell: every species ever reached, including earlier forms."""

    species_id: int
    name: str
    rarity: Rarity
    count: int
    is_shiny: bool
    has_normal: bool
    # Only the current form of the Pokémon being raised.
    is_raising: bool
    first_caught_at: str | None
    search_names: list[str] = Field(default_factory=list)


class UnownFormView(BaseModel):
    form: str
    symbol: str
    is_shiny: bool
    has_normal: bool
    is_raising: bool


class CollectionView(BaseModel):
    total: int
    counts_by_rarity: dict[str, int] = Field(default_factory=dict)
    catch_log: list[DexEntryView] = Field(default_factory=list)
    pokedex: list[DexSpeciesView] = Field(default_factory=list)
    unown_forms: list[UnownFormView] = Field(default_factory=list)


class StatView(BaseModel):
    stat: str
    base: int
    iv: int | None = None
    value: int | None = None


class MoveMethodView(BaseModel):
    method: str
    level: int


class MoveView(BaseModel):
    name: str
    methods: list[MoveMethodView] = Field(default_factory=list)


class KnownMoveView(BaseModel):
    name: str
    learned_at_level: int


class IndividualView(BaseModel):
    id: str
    is_active: bool
    is_shiny: bool
    caught_at: str | None
    level: int
    gender: str | None
    nature: str | None
    nature_label: str | None
    ability_name: str | None
    ability_is_hidden: bool
    unown_form: str | None = None
    stats: list[StatView] = Field(default_factory=list)
    stat_scale: int = 300
    moves: list[KnownMoveView] = Field(default_factory=list)


class AbilityView(BaseModel):
    name: str
    is_hidden: bool


class PokemonDetailView(BaseModel):
    species_id: int
    name: str
    rarity: Rarity | None
    types: list[str] = Field(default_factory=list)
    height_m: float
    weight_kg: float
    base_total: int
    base_stats: list[StatView] = Field(default_factory=list)
    abilities: list[AbilityView] = Field(default_factory=list)
    moves: list[MoveView] = Field(default_factory=list)
    individuals: list[IndividualView] = Field(default_factory=list)


class RecapBucketView(BaseModel):
    key: str
    tokens: int
    is_current: bool
    has_data: bool


class RecapGraduateView(BaseModel):
    species_id: int
    name: str
    is_shiny: bool
    unown_form: str | None = None


class RecapView(BaseModel):
    scope: str
    offset: int
    start: str
    end: str
    total: int
    buckets: list[RecapBucketView] = Field(default_factory=list)
    is_in_progress: bool
    previous_total: int | None
    delta: float | None
    best_day: str | None
    best_day_tokens: int
    active_days: int
    counted_days: int
    best_streak: int
    graduated_count: int
    graduated: list[RecapGraduateView] = Field(default_factory=list)
    can_go_back: bool


class SnapshotView(BaseModel):
    id: str
    created_at: str
    dex_count: int
    lifetime_tokens: int
    current_species_id: int | None
    current_is_shiny: bool


class MetaView(BaseModel):
    last_refresh: str | None
    poll_interval: float
    timezone: str
    # Short code ("ja", not the stored "ja-Hrkt") so the UI can match its options.
    language: str = "en"

    providers: list[str] = Field(default_factory=list)
    log_roots: list[str] = Field(default_factory=list)
    log_roots_present: list[str] = Field(default_factory=list)
    log_files_found: int = 0
    # Set when an all-zero reading has a cause worth telling the user about.
    source_warning: str | None = None
    version: str = "1.0.0"

    growth_difficulty: float = 1.0
    shop_difficulty: float = 1.0
    difficulty_min: float = 0.1
    difficulty_max: float = 2.0
    limit_display: str = "used"
    crit_threshold: float = 95.0


class StateView(BaseModel):
    companion: CompanionView
    usage: UsageView
    limits: LimitsView
    wallet: WalletView
    bag: list[BagItemView] = Field(default_factory=list)
    shop: ShopView
    collection: CollectionView
    meta: MetaView


class LanguageRequest(BaseModel):
    language: str


class DifficultyRequest(BaseModel):
    growth: float
    shop: float


class LimitDisplayRequest(BaseModel):
    mode: str


class UseCandyRequest(BaseModel):
    count: int = 1


class BuyItemRequest(BaseModel):
    kind: str


class BuyEggRequest(BaseModel):
    tier: Rarity | None = None


class ActionResult(BaseModel):
    ok: bool
    message: str | None = None
