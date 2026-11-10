"""Per-region Pokédex completion and its cosmetic badges.

Purely cosmetic: nothing here touches growth, prices or hatch odds. Badges are computed
from the collection every time; the save only remembers which ones were announced, so
a completion is announced once per save. "Region", not "generation": the companion
already uses generation for its hatch-attempt counter.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import forms

if TYPE_CHECKING:
    from .state import SaveState


@dataclass(frozen=True)
class Region:
    key: str
    name: str
    first: int
    last: int

    @property
    def total(self) -> int:
        return self.last - self.first + 1

    def contains(self, species_id: int) -> bool:
        return self.first <= species_id <= self.last


REGIONS: tuple[Region, ...] = (
    Region("kanto", "Kanto", 1, 151),
    Region("johto", "Johto", 152, 251),
    Region("hoenn", "Hoenn", 252, 386),
    Region("sinnoh", "Sinnoh", 387, 493),
    Region("unova", "Unova", 494, 649),
)
NATIONAL = Region("national", "National", 1, 649)
UNOWN_TOTAL = len(forms.forms_of(forms.UNOWN_SPECIES_ID))


def region_for(species_id: int) -> Region | None:
    return next((r for r in REGIONS if r.contains(species_id)), None)


@dataclass(frozen=True)
class RegionCompletion:
    region: Region
    owned: int
    shiny_owned: int

    @property
    def total(self) -> int:
        return self.region.total

    @property
    def is_complete(self) -> bool:
        return self.owned >= self.total

    @property
    def is_shiny_complete(self) -> bool:
        return self.shiny_owned >= self.total


def _owned_chains(state: SaveState) -> list[tuple[Sequence[int], bool, str | None]]:
    """Every record's chain (graduated and released) and the active Pokémon's reached stages."""
    chains: list[tuple[Sequence[int], bool, str | None]] = [
        (e.chain_order, e.is_shiny, e.form) for e in state.dex
    ]
    a = state.active
    if a is not None:
        chains.append((a.path_ids[: a.stage_index + 1], a.current_is_shiny, a.form))
    return chains


def owned_species(state: SaveState) -> dict[int, bool]:
    """Species id -> owned shiny by any record. Forms collapse into their species."""
    owned: dict[int, bool] = {}
    for chain, shiny, _ in _owned_chains(state):
        for sid in chain:
            owned[sid] = owned.get(sid, False) or shiny
    return owned


def owned_forms(state: SaveState) -> set[tuple[int, str]]:
    """(species, form) pairs reached by any recorded or active Pokémon, shiny or not."""
    out: set[tuple[int, str]] = set()
    for chain, _, form in _owned_chains(state):
        for sid in chain:
            resolved = forms.resolve(sid, form)
            if resolved is not None:
                out.add((sid, resolved))
    return out


def unown_forms(state: SaveState) -> int:
    return sum(1 for sid, _ in owned_forms(state) if sid == forms.UNOWN_SPECIES_ID)


def completion(region: Region, owned: Mapping[int, bool]) -> RegionCompletion:
    ids = [sid for sid in owned if region.contains(sid)]
    return RegionCompletion(region, len(ids), sum(1 for sid in ids if owned[sid]))


def region_completions(owned: Mapping[int, bool]) -> list[RegionCompletion]:
    return [completion(r, owned) for r in REGIONS]


@dataclass(frozen=True)
class DexBadge:
    kind: str  # "region" | "regionShiny" | "unown" | "national" | "nationalShiny"
    region: Region | None = None

    @property
    def key(self) -> str:
        """Written to the save: never change these."""
        return f"{self.kind}.{self.region.key}" if self.region else self.kind

    @property
    def emoji(self) -> str:
        return {"region": "🏅", "unown": "🔤", "national": "🌍"}.get(self.kind, "✨")

    @property
    def is_regional(self) -> bool:
        return self.region is not None

    @property
    def label(self) -> str:
        if self.kind == "region":
            return f"{self.region.name} Dex complete"
        if self.kind == "regionShiny":
            return f"Shiny {self.region.name} Dex complete"
        if self.kind == "unown":
            return "Unown collection complete"
        if self.kind == "national":
            return "National Dex complete"
        return "Shiny National Dex complete"

    @property
    def title(self) -> str:
        return f"{self.emoji} {self.label}!"

    @property
    def body(self) -> str:
        if self.kind == "region":
            return f"You've caught every Pokémon in {self.region.name}!"
        if self.kind == "regionShiny":
            return f"You've caught a shiny of every Pokémon in {self.region.name}!"
        if self.kind == "unown":
            return f"You've collected all {UNOWN_TOTAL} Unown forms!"
        if self.kind == "national":
            return "You've caught every Pokémon from Gen 1–5!"
        return "You've caught a shiny of every Pokémon!"


def earned_badges(owned: Mapping[int, bool], unown_count: int) -> list[DexBadge]:
    """Every badge earned, in announcement order."""
    out: list[DexBadge] = []
    for c in region_completions(owned):
        if c.is_complete:
            out.append(DexBadge("region", c.region))
        if c.is_shiny_complete:
            out.append(DexBadge("regionShiny", c.region))
    if unown_count >= UNOWN_TOTAL:
        out.append(DexBadge("unown"))
    national = completion(NATIONAL, owned)
    if national.is_complete:
        out.append(DexBadge("national"))
    if national.is_shiny_complete:
        out.append(DexBadge("nationalShiny"))
    return out


def newly_earned(earned: Iterable[DexBadge], notified: Iterable[str]) -> list[DexBadge]:
    seen = set(notified)
    return [b for b in earned if b.key not in seen]


def state_badges(state: SaveState) -> list[DexBadge]:
    return earned_badges(owned_species(state), unown_forms(state))


def seed_completions(state: SaveState) -> bool:
    """Mark what is already complete as announced, once. True when keys were added.

    Runs at start-up and on every import, so a save from before this feature does not
    announce its old completions, and does not lose the ones earned after it.
    """
    if state.dex_completions_seeded:
        return False
    state.dex_completions_seeded = True
    keys = {b.key for b in state_badges(state)}
    if not keys:
        return False
    state.notified_dex_completions = sorted(set(state.notified_dex_completions) | keys)
    return True


def check_completions(state: SaveState) -> list[DexBadge]:
    """Badges earned since the last check, recorded as announced. None before seeding."""
    if not state.dex_completions_seeded:
        return []
    fresh = newly_earned(state_badges(state), state.notified_dex_completions)
    if fresh:
        state.notified_dex_completions = sorted(
            set(state.notified_dex_completions) | {b.key for b in fresh}
        )
    return fresh
