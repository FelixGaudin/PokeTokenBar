"""Token economy and randomisation constants, ported from the macOS app.

Every threshold here is a balance decision, not an implementation detail — keep the
values in sync with the upstream `PokemonBalance` / `RareCandy` / `Mint` /
`ShinyCharm` / `FreshEgg` enums so saves and progression feel identical.
"""

from __future__ import annotations

import math
from enum import Enum


def round_half_up(x: float) -> int:
    """Swift's `.rounded()` for non-negative values. Python's round() is banker's."""
    return math.floor(x + 0.5)


class Rarity(str, Enum):
    COMMON = "common"
    UNCOMMON = "uncommon"
    RARE = "rare"
    LEGENDARY = "legendary"

    @property
    def sort_rank(self) -> int:
        return {"common": 0, "uncommon": 1, "rare": 2, "legendary": 3}[self.value]

    @property
    def capture_rate_ceiling(self) -> int | None:
        """Highest capture_rate that still counts as this tier or better.

        `None` for legendary: that tier is decided by the is_legendary / is_mythical
        flags, which the base-species index does not carry, so a legendary-only egg
        cannot be expressed as a capture-rate filter (and is not sold).
        """
        return {"rare": 45, "uncommon": 120, "common": 255, "legendary": None}[self.value]

    def includes(self, capture_rate: int) -> bool:
        ceiling = self.capture_rate_ceiling
        return ceiling is not None and capture_rate <= ceiling

    @staticmethod
    def classify(capture_rate: int, is_legendary: bool, is_mythical: bool) -> "Rarity":
        if is_legendary or is_mythical:
            return Rarity.LEGENDARY
        if Rarity.RARE.includes(capture_rate):
            return Rarity.RARE
        if Rarity.UNCOMMON.includes(capture_rate):
            return Rarity.UNCOMMON
        return Rarity.COMMON


# Tokens the egg must absorb before it hatches. Overflow carries into the hatchling.
EGG_HATCH_THRESHOLD = 5_000_000

_GRADUATION_TOTAL = {
    Rarity.COMMON: 750_000_000,
    Rarity.UNCOMMON: 1_875_000_000,
    Rarity.RARE: 3_000_000_000,
    Rarity.LEGENDARY: 6_000_000_000,
}


def graduation_total(rarity: Rarity) -> int:
    return _GRADUATION_TOTAL[rarity]


# A line that has graduated before grows twice as fast on every later hatch.
REPEAT_GROWTH_MULTIPLIER = 2


def phase_threshold(
    rarity: Rarity, total_forms: int, stage_index: int, growth_multiplier: int = 1
) -> int:
    """Tokens needed to leave `stage_index`, at the default difficulty.

    A line with k forms splits its graduation total T so that form i costs
    T*i / (k(k+1)/2) — the sum is exactly T, and each stage costs more than the last.
    Same rarity means the same total regardless of how many stages the line has.
    """
    k = max(1, total_forms)
    i = stage_index + 1
    denom = k * (k + 1) / 2.0
    standard = round_half_up(graduation_total(rarity) * i / denom)
    return max(1, round_half_up(standard / max(1, growth_multiplier)))


# Difficulty: two independent multipliers of the default balance. Growth scales the
# egg and stage thresholds, shop scales prices. Rare Candy XP is never scaled, or it
# would cancel against the thresholds it feeds.
DIFFICULTY_MIN = 0.1
DIFFICULTY_MAX = 2.0
DEFAULT_DIFFICULTY = 1.0


def clamp_difficulty(value: float) -> float:
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return DEFAULT_DIFFICULTY
    return min(max(float(value), DIFFICULTY_MIN), DIFFICULTY_MAX)


def scaled(base: int, difficulty: float) -> int:
    return round_half_up(base * clamp_difficulty(difficulty))


def collection_weight(weight: int, collected: bool) -> int:
    """Halve the pull of something already owned, without ever reaching zero."""
    return max(1, weight // 2) if collected else max(1, weight)


class ItemKind(str, Enum):
    RARE_CANDY = "rareCandy"
    MINT = "mint"
    SHINY_CHARM = "shinyCharm"

    @property
    def sprite_name(self) -> str | None:
        # PokéAPI has no mint sprite (gen-8 item) — the UI falls back to the emoji.
        return {"rareCandy": "rare-candy", "mint": None, "shinyCharm": "shiny-charm"}[self.value]

    @property
    def emoji(self) -> str:
        return {"rareCandy": "🍬", "mint": "🌿", "shinyCharm": "✨"}[self.value]

    @property
    def is_passive(self) -> bool:
        """Held items apply while owned instead of being consumed; bought once."""
        return self is ItemKind.SHINY_CHARM

    @property
    def shop_price(self) -> int:
        return {
            "rareCandy": RARE_CANDY_PRICE,
            "mint": MINT_PRICE,
            "shinyCharm": SHINY_CHARM_PRICE,
        }[self.value]


# Rare candy: XP injected into the active Pokémon. Smaller than the cheapest first
# evolution (common 1-form = 125M) so one candy can never chain two stages.
RARE_CANDY_XP = 100_000_000
# Granted when a weekly limit window fills up (a session window grants 1).
RARE_CANDY_WEEKLY_GRANT = 5
# Priced at 5x its XP value so the free grant is always the better deal.
RARE_CANDY_PRICE = 500_000_000

# Nature re-roll. Purely cosmetic, so the price is a feel value — a fifth of a candy.
MINT_PRICE = 100_000_000

# Permanent luck upgrade, priced at one rare graduation.
SHINY_CHARM_PRICE = 3_000_000_000

# Fresh egg: discards the active Pokémon and re-incubates from zero.
FRESH_EGG_PRICE = 1_000_000_000
# Tiers on sale. No legendary-only egg: that tier cannot be expressed as a
# capture-rate floor, and the top tier is not sold as a guarantee.
FRESH_EGG_SHOP_TIERS: list[Rarity | None] = [None, Rarity.UNCOMMON, Rarity.RARE]
# Guaranteed-tier prices reuse the graduation-total ratios (1 : 2.5 : 4).
_FRESH_EGG_TIER_PRICE = {
    None: FRESH_EGG_PRICE,
    Rarity.UNCOMMON: 2_500_000_000,
    Rarity.RARE: 4_000_000_000,
}


def fresh_egg_price(tier: Rarity | None) -> int:
    return _FRESH_EGG_TIER_PRICE[tier]


# Shiny odds. The charm lowers the denominator (1/64 -> 1/48, about +33%).
SHINY_DENOMINATOR = 64
SHINY_DENOMINATOR_WITH_CHARM = 48


def shiny_denominator(charm_owned: bool) -> int:
    return SHINY_DENOMINATOR_WITH_CHARM if charm_owned else SHINY_DENOMINATOR
# A common multi-stage hatch has this chance of secretly being a disguised Ditto.
DITTO_DISGUISE_DENOMINATOR = 128
DITTO_SPECIES_ID = 132

# PokéAPI only ships animated Gen-V sprites up to national dex #649.
ANIMATED_SPECIES_MAX = 649

# Unown hatches as one of 28 letters. Uncollected letters weigh 2, collected ones 1.
UNOWN_SPECIES_ID = 201
UNOWN_FORMS = [chr(c) for c in range(ord("a"), ord("z") + 1)] + ["exclamation", "question"]
UNOWN_SYMBOLS = {f: (f.upper() if len(f) == 1 else {"exclamation": "!", "question": "?"}[f])
                 for f in UNOWN_FORMS}


def resolve_unown_form(species_id: int, form: str | None) -> str | None:
    """Unown always has a letter (legacy records read as A); nothing else has one."""
    if species_id != UNOWN_SPECIES_ID:
        return None
    return form if form in UNOWN_SYMBOLS else "a"


def roll_unown_form(r: int, collected: set[str]) -> str:
    weights = [collection_weight(2, f in collected) for f in UNOWN_FORMS]
    remaining = r % sum(weights)
    for form, weight in zip(UNOWN_FORMS[:-1], weights[:-1], strict=True):
        remaining -= weight
        if remaining < 0:
            return form
    return UNOWN_FORMS[-1]


def display_name(name: str, species_id: int, form: str | None) -> str:
    resolved = resolve_unown_form(species_id, form)
    return f"{name} [{UNOWN_SYMBOLS[resolved]}]" if resolved else name


def sprite_name(species_id: int, form: str | None) -> str:
    """PokéAPI sprite file stem: Unown A keeps the plain id, other letters get a suffix."""
    resolved = resolve_unown_form(species_id, form)
    return f"{species_id}-{resolved}" if resolved and resolved != "a" else str(species_id)

NATURES = [
    "hardy", "lonely", "brave", "adamant", "naughty",
    "bold", "docile", "relaxed", "impish", "lax",
    "timid", "hasty", "serious", "jolly", "naive",
    "modest", "mild", "quiet", "bashful", "rash",
    "calm", "gentle", "sassy", "careful", "quirky",
]

NATURE_LABELS = {
    "hardy": "Hardy", "lonely": "Lonely", "brave": "Brave", "adamant": "Adamant",
    "naughty": "Naughty", "bold": "Bold", "docile": "Docile", "relaxed": "Relaxed",
    "impish": "Impish", "lax": "Lax", "timid": "Timid", "hasty": "Hasty",
    "serious": "Serious", "jolly": "Jolly", "naive": "Naive", "modest": "Modest",
    "mild": "Mild", "quiet": "Quiet", "bashful": "Bashful", "rash": "Rash",
    "calm": "Calm", "gentle": "Gentle", "sassy": "Sassy", "careful": "Careful",
    "quirky": "Quirky",
}


def has_animated_sprite(species_id: int) -> bool:
    return 1 <= species_id <= ANIMATED_SPECIES_MAX
