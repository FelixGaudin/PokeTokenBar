"""Persistent individual values for each Pokémon, and the stats they imply.

A profile is rolled once at hatch from a seed and kept for life: through evolution,
graduation, release and save transfer. Everything species-dependent (gender, ability,
moves) is filled in later from PokéAPI details, deterministically from the same seed,
so a profile enriched twice — or on another machine — comes out identical.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from . import balance as B

MAX_TOKEN_VALUE = 1_000_000_000_000_000
_U64 = (1 << 64) - 1
_GOLDEN = 0x9E3779B97F4A7C15
_ENRICH_SALT = 0xA11B1E5D9EED

STAT_KEYS = ("hp", "attack", "defense", "special-attack", "special-defense", "speed")

# (raised, lowered) by 10%; the five missing natures are neutral.
NATURE_MODIFIERS: dict[str, tuple[str, str]] = {
    "lonely": ("attack", "defense"),
    "brave": ("attack", "speed"),
    "adamant": ("attack", "special-attack"),
    "naughty": ("attack", "special-defense"),
    "bold": ("defense", "attack"),
    "relaxed": ("defense", "speed"),
    "impish": ("defense", "special-attack"),
    "lax": ("defense", "special-defense"),
    "timid": ("speed", "attack"),
    "hasty": ("speed", "defense"),
    "jolly": ("speed", "special-attack"),
    "naive": ("speed", "special-defense"),
    "modest": ("special-attack", "attack"),
    "mild": ("special-attack", "defense"),
    "quiet": ("special-attack", "speed"),
    "rash": ("special-attack", "special-defense"),
    "calm": ("special-defense", "attack"),
    "gentle": ("special-defense", "defense"),
    "sassy": ("special-defense", "speed"),
    "careful": ("special-defense", "special-attack"),
}


class SplitMix64:
    def __init__(self, seed: int) -> None:
        self.state = (seed & _U64) or _GOLDEN

    def next(self) -> int:
        self.state = (self.state + _GOLDEN) & _U64
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _U64
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _U64
        return z ^ (z >> 31)


def fnv1a64(text: str) -> int:
    h = 0xCBF29CE484222325
    for byte in text.encode("utf-8"):
        h ^= byte
        h = (h * 0x00000100000001B3) & _U64
    return h


@dataclass(frozen=True)
class Ability:
    name: str
    slot: int
    is_hidden: bool


@dataclass(frozen=True)
class LearnMethod:
    method: str
    level: int


@dataclass(frozen=True)
class MoveInfo:
    name: str
    learn_methods: list[LearnMethod]


@dataclass
class PokemonDetails:
    """Immutable PokéAPI metadata for one species. Never part of the save."""

    species_id: int
    name: str
    height: int  # decimetres
    weight: int  # hectograms
    base_experience: int | None
    gender_rate: int  # female eighths, -1 genderless
    types: list[str]
    base_stats: dict[str, int]
    abilities: list[Ability]
    moves: list[MoveInfo] = field(default_factory=list)

    def level_up_moves(self, level: int) -> list[tuple[str, int]]:
        """Every level-up move learnable by `level`, at its earliest level."""
        best: dict[str, int] = {}
        for move in self.moves:
            for m in move.learn_methods:
                if m.method == "level-up" and m.level <= level:
                    best[move.name] = min(best.get(move.name, m.level), m.level)
        return sorted(best.items(), key=lambda kv: (kv[1], kv[0]))


class IVs(BaseModel):
    hp: int = 0
    attack: int = 0
    defense: int = 0
    special_attack: int = 0
    special_defense: int = 0
    speed: int = 0

    def get(self, stat: str) -> int:
        return getattr(self, stat.replace("-", "_"))


class KnownMove(BaseModel):
    name: str
    learned_at_level: int


class PokemonProfile(BaseModel):
    instance_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    seed: int = 0
    gender: str | None = None  # "male" | "female" | "genderless"
    ivs: IVs = Field(default_factory=IVs)
    ability_slot: int | None = None
    ability_name: str | None = None
    ability_is_hidden: bool = False
    level: int = 5
    # Earned growth in default-balance units, whatever the difficulty or boost.
    growth_tokens: int = 0
    moves: list[KnownMove] = Field(default_factory=list)

    @staticmethod
    def generate(
        seed: int, growth_tokens: int = 0, instance_id: str | None = None
    ) -> "PokemonProfile":
        r = SplitMix64(seed)
        values = [r.next() % 32 for _ in STAT_KEYS]
        return PokemonProfile(
            instance_id=instance_id or str(uuid.uuid4()),
            seed=seed & _U64,
            ivs=IVs(**{k.replace("-", "_"): v for k, v in zip(STAT_KEYS, values, strict=True)}),
            growth_tokens=max(0, growth_tokens),
        )

    # -- growth ------------------------------------------------------------

    def advance_growth(self, candidate: int, rarity: B.Rarity) -> None:
        """High-water mark: neither growth nor level ever goes down."""
        self.growth_tokens = min(MAX_TOKEN_VALUE, max(self.growth_tokens, max(0, candidate)))
        progress = min(1.0, self.growth_tokens / max(1, B.graduation_total(rarity)))
        self.level = min(100, max(self.level, max(5, 5 + math.floor(progress * 95))))

    def rebase_for_species(self, old: B.Rarity, new: B.Rarity) -> None:
        """A Ditto reveal changes the species: keep identity and IVs, re-derive the rest."""
        fraction = min(1.0, max(0, self.growth_tokens) / B.graduation_total(old))
        rebased = math.floor(fraction * B.graduation_total(new))
        self.gender = None
        self.ability_slot = None
        self.ability_name = None
        self.ability_is_hidden = False
        self.moves = []
        self.growth_tokens = rebased
        self.level = 5
        self.advance_growth(rebased, new)

    # -- species-dependent fields -----------------------------------------

    def enrich(self, details: PokemonDetails) -> None:
        r = SplitMix64(self.seed ^ _ENRICH_SALT)
        if self.gender is None:
            if details.gender_rate < 0:
                self.gender = "genderless"
            else:
                self.gender = "female" if r.next() % 8 < details.gender_rate else "male"

        by_slot = sorted(details.abilities, key=lambda a: a.slot)
        normal = [a for a in by_slot if not a.is_hidden]
        hidden = [a for a in by_slot if a.is_hidden]
        if self.ability_slot is None and by_slot:
            if hidden and r.next() % 128 == 0:
                chosen = hidden[r.next() % len(hidden)]
            elif normal:
                chosen = normal[r.next() % len(normal)]
            else:
                chosen = by_slot[0]
            self.ability_slot = chosen.slot
            self.ability_is_hidden = chosen.is_hidden

        # Repair the name from the slot, so a species change or stale flag heals.
        repaired = (
            next(
                (
                    a
                    for a in by_slot
                    if a.slot == self.ability_slot and a.is_hidden == self.ability_is_hidden
                ),
                None,
            )
            or next((a for a in by_slot if a.slot == self.ability_slot), None)
            or (normal[0] if normal else None)
            or (hidden[0] if hidden else None)
        )
        if repaired is not None:
            self.ability_name = repaired.name
            self.ability_slot = repaired.slot
            self.ability_is_hidden = repaired.is_hidden

        self.moves = [
            KnownMove(name=name, learned_at_level=lvl)
            for name, lvl in details.level_up_moves(self.level)[-4:]
        ]

    def sanitize(self) -> None:
        """Clamp everything an imported save could have hand-edited."""
        self.level = min(100, max(5, self.level))
        self.growth_tokens = min(MAX_TOKEN_VALUE, max(0, self.growth_tokens))
        for key in STAT_KEYS:
            attr = key.replace("-", "_")
            setattr(self.ivs, attr, min(31, max(0, getattr(self.ivs, attr))))
        self.moves = [
            KnownMove(name=m.name[:80], learned_at_level=min(100, max(0, m.learned_at_level)))
            for m in self.moves[:4]
        ]
        if not self.instance_id:
            self.instance_id = str(uuid.uuid4())


def reconstructed_growth(rarity: B.Rarity, total_forms: int, completed: int, usage: int) -> int:
    done = sum(
        B.phase_threshold(rarity, total_forms, s)
        for s in range(min(max(0, completed), total_forms))
    )
    return min(MAX_TOKEN_VALUE, done + min(MAX_TOKEN_VALUE, max(0, usage)))


def nature_multiplier(nature: str | None, stat: str) -> float:
    up_down = NATURE_MODIFIERS.get(nature or "")
    if up_down is None:
        return 1.0
    if stat == up_down[0]:
        return 1.1
    if stat == up_down[1]:
        return 0.9
    return 1.0


def actual_stat(stat: str, base: int, iv: int, level: int, nature: str | None) -> int:
    core = ((2 * base + iv) * level) // 100
    if stat == "hp":
        return core + level + 10
    return math.floor((core + 5) * nature_multiplier(nature, stat))


def stat_bar_scale(values: list[int]) -> int:
    top = max([300, *values])
    return -(-top // 100) * 100
