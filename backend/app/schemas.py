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


class EggView(BaseModel):
    usage: int
    threshold: int
    progress: float
    tokens_to_hatch: int
    guaranteed_tier: Rarity | None = None


class CompanionView(BaseModel):
    display_state: str
    egg: EggView
    active: ActiveView | None
    event: dict | None = None
    just_evolved_to: str | None = None
    just_graduated: str | None = None


class PeriodView(BaseModel):
    total_tokens: int
    cost: float
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


class DayPoint(BaseModel):
    date: str
    total_tokens: int
    cost: float


class UsageView(BaseModel):
    today_date: str
    today: PeriodView
    week: PeriodView
    month: PeriodView
    block: BlockView | None
    burn_per_minute: float
    burn_tier: str
    daily_history: list[DayPoint] = Field(default_factory=list)
    scanned_files: int = 0


class LimitWindowView(BaseModel):
    key: str
    name: str
    kind: str
    utilization: float
    resets_at: str | None = None


class LimitsView(BaseModel):
    available: bool
    enabled: bool
    # True when these are the last good numbers and the latest fetch failed.
    stale: bool = False
    plan: str | None = None
    windows: list[LimitWindowView] = Field(default_factory=list)
    error: str | None = None
    auth_expired: bool = False
    fetched_at: str | None = None


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


class ShopView(BaseModel):
    items: list[ShopItemView] = Field(default_factory=list)
    eggs: list[ShopEggView] = Field(default_factory=list)


class DexChainSpecies(BaseModel):
    species_id: int
    name: str


class DexEntryView(BaseModel):
    base_id: int
    final_id: int
    final_name: str
    rarity: Rarity
    is_shiny: bool
    nature: str | None
    nature_label: str | None
    caught_at: str | None
    chain: list[DexChainSpecies] = Field(default_factory=list)


class DexSpeciesView(BaseModel):
    """One row of the Pokédex: a final form, however many times it was caught."""

    final_id: int
    name: str
    rarity: Rarity
    count: int
    shiny_count: int
    first_caught_at: str | None


class CollectionView(BaseModel):
    total: int
    counts_by_rarity: dict[str, int] = Field(default_factory=dict)
    catch_log: list[DexEntryView] = Field(default_factory=list)
    pokedex: list[DexSpeciesView] = Field(default_factory=list)


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


class BuyItemRequest(BaseModel):
    kind: str


class BuyEggRequest(BaseModel):
    tier: Rarity | None = None


class ActionResult(BaseModel):
    ok: bool
    message: str | None = None
